from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models.blog import BlogPost, BlogCategory, BlogTag, BlogComment
from app.extensions import db
from datetime import datetime, timezone
from slugify import slugify
from sqlalchemy import desc, or_
from flask import request
from app.auth.utils import require_roles
from app.models.user import User
from app.validation import json_object, text

blog_ns = Namespace('blog', description='Blog operations')

# ── Swagger models ────────────────────────────────────────────────────────────

category_model = blog_ns.model('BlogCategory', {
    'id':          fields.Integer(readonly=True),
    'name':        fields.String(required=True),
    'slug':        fields.String(readonly=True),
    'description': fields.String(),
})

tag_model = blog_ns.model('BlogTag', {
    'id':   fields.Integer(readonly=True),
    'name': fields.String(required=True),
    'slug': fields.String(readonly=True),
})

author_model = blog_ns.model('BlogAuthor', {
    'id':         fields.Integer(readonly=True),
    'name':       fields.String(readonly=True),
    'username':   fields.String(readonly=True),
    'email':      fields.String(readonly=True),
    'is_admin':   fields.Boolean(readonly=True),
})

comment_model = blog_ns.model('BlogComment', {
    'id':         fields.Integer(readonly=True),
    'content':    fields.String(required=True),
    'author_id':  fields.Integer(readonly=True),
    'created_at': fields.DateTime(readonly=True),
    'updated_at': fields.DateTime(readonly=True),
})

post_model = blog_ns.model('BlogPost', {
    'id':             fields.Integer(readonly=True),
    'title':          fields.String(required=True),
    'slug':           fields.String(readonly=True),
    'content':        fields.String(required=True),
    'excerpt':        fields.String(),
    'featured_image': fields.String(),
    'status':         fields.String(enum=['draft', 'published'], default='draft'),
    'views':          fields.Integer(readonly=True),
    'author_id':      fields.Integer(readonly=True),
    'category_id':    fields.Integer(required=True),
    'tag_ids':        fields.List(fields.Integer, description='Tag IDs (write-only)'),
    'tags':           fields.List(fields.Nested(tag_model), readonly=True),
    'created_at':     fields.DateTime(readonly=True),
    'updated_at':     fields.DateTime(readonly=True),
    'published_at':   fields.DateTime(readonly=True),
})


# ── helpers ───────────────────────────────────────────────────────────────────

def _unique_slug(base: str, exclude_id: int | None = None) -> str:
    slug, n = base, 1
    while True:
        q = BlogPost.query.filter_by(slug=slug)
        if exclude_id:
            q = q.filter(BlogPost.id != exclude_id)
        if not q.first():
            return slug
        slug = f"{base}-{n}"
        n += 1


def _attach_tags(post: BlogPost, tag_ids: list[int]) -> None:
    post.tags = []
    for tid in tag_ids:
        tag = db.session.get(BlogTag, tid)
        if tag:
            post.tags.append(tag)


# ── Writable field whitelist ────────────────────────────────────────────
#
# ``BlogPost(**payload)`` and a blind setattr loop accepted any attribute the
# model happened to have. Dropping a hand-picked list of read-only keys is not
# the same guarantee: anything not on that list — 'views', a relationship, a
# typo — still reached the row or raised a 500. Every writable field is named
# here, with the bound it is checked against.

_POST_STRINGS = {'title': 200, 'content': 50000, 'excerpt': 500}

_POST_WRITABLE = set(_POST_STRINGS) | {'featured_image', 'status', 'category_id', 'tag_ids'}

#: Server-owned, derived or display-only keys. The editor PUTs back the record
#: it just fetched, so these are dropped rather than refused — but never
#: written. 'cover_image' is the read alias _post_to_dict adds.
_POST_IGNORED = {'id', 'slug', 'views', 'author', 'author_id', 'category',
                 'tags', 'cover_image', 'created_at', 'updated_at', 'published_at'}

_POST_STATUSES = ('draft', 'published')

#: An <img src> the frontend renders. A javascript: or data: URL here is stored
#: XSS, so only absolute http(s) and site-relative paths are accepted.
_IMAGE_PREFIXES = ('http://', 'https://', '/')


def _featured_image(value):
    if value in (None, ''):
        return None
    url = text(value, 'featured_image', 255)
    if not url.startswith(_IMAGE_PREFIXES):
        blog_ns.abort(400, "'featured_image' must be an http(s) or site-relative URL")
    return url


def _tag_ids(value):
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > 50
            or any(isinstance(t, bool) or not isinstance(t, int) for t in value)):
        blog_ns.abort(400, "'tag_ids' must be a list of at most 50 integer tag ids")
    return value


def _clean_post(data):
    """Validate a post payload and return only the fields a client may write."""
    unknown = set(data) - _POST_WRITABLE - _POST_IGNORED
    if unknown:
        blog_ns.abort(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    clean = {}
    for field, limit in _POST_STRINGS.items():
        if field in data:
            value, required = data[field], field in ('title', 'content')
            clean[field] = None if value is None and not required else text(value, field, limit, required)
    if 'featured_image' in data:
        clean['featured_image'] = _featured_image(data['featured_image'])
    if 'status' in data:
        if data['status'] not in _POST_STATUSES:
            blog_ns.abort(400, f"'status' must be one of {', '.join(_POST_STATUSES)}")
        clean['status'] = data['status']
    if 'category_id' in data:
        category_id = data['category_id']
        if isinstance(category_id, bool) or not isinstance(category_id, int):
            blog_ns.abort(400, "'category_id' must be an integer")
        clean['category_id'] = category_id
    return clean


def _category_or_404(category_id):
    category = db.session.get(BlogCategory, category_id)
    if not category:
        blog_ns.abort(404, f"Category {category_id} not found")
    return category


#: Categories and tags derive their slug server-side, so only the display
#: fields are writable. Everything else is read-only or a relationship.
_CATEGORY_STRINGS = {'name': 100, 'description': 2000}
_CATEGORY_IGNORED = {'id', 'slug', 'created_at', 'posts', 'posts_count'}
_TAG_STRINGS = {'name': 50}
_TAG_IGNORED = {'id', 'slug', 'posts'}


def _clean_named(data, strings, ignored):
    """Validate a category or tag payload down to its writable fields."""
    unknown = set(data) - set(strings) - ignored
    if unknown:
        blog_ns.abort(400, f"Unknown field(s): {', '.join(sorted(unknown))}")
    clean = {}
    for field, limit in strings.items():
        if field in data:
            value, required = data[field], field == 'name'
            clean[field] = None if value is None and not required else text(value, field, limit, required)
    return clean


def _unique_taxonomy_slug(model, base, exclude_id=None):
    slug, n = base, 1
    while True:
        query = model.query.filter_by(slug=slug)
        if exclude_id:
            query = query.filter(model.id != exclude_id)
        if not query.first():
            return slug
        slug = f"{base}-{n}"
        n += 1


def _post_to_dict(post: BlogPost) -> dict:
    """Serialize a BlogPost to a dict the frontend expects."""
    author = post.author
    category = post.category
    return {
        'id':             post.id,
        'title':          post.title,
        'slug':           post.slug,
        'content':        post.content,
        'excerpt':        post.excerpt,
        'featured_image': post.featured_image,
        'cover_image':    post.featured_image,   # alias expected by some components
        'status':         post.status,
        'views':          post.views,
        'author_id':      post.author_id,
        # The author block is public on every published post, so it carries a
        # display name only. Exposing the editor's address here handed anyone
        # scraping the blog a list of staff emails to phish.
        'author': {
            'id':       author.id,
            'name':     f"{author.first_name} {author.last_name}".strip() or 'Moonlight Freight',
            'username': f"{author.first_name} {author.last_name}".strip() or 'Moonlight Freight',
            'is_admin': True,
        } if author else None,
        'category_id': post.category_id,
        'category': {
            'id':          category.id,
            'name':        category.name,
            'slug':        category.slug,
            'description': category.description,
        } if category else None,
        'tag_ids': [t.id for t in post.tags],
        'tags':    [{'id': t.id, 'name': t.name, 'slug': t.slug} for t in post.tags],
        'created_at':   post.created_at.isoformat() if post.created_at else None,
        'updated_at':   post.updated_at.isoformat() if post.updated_at else None,
        'published_at': post.published_at.isoformat() if post.published_at else None,
    }


# ── /posts ────────────────────────────────────────────────────────────────────

@blog_ns.route('/posts')
class BlogPostList(Resource):

    def get(self):
        """
        List blog posts with pagination, filtering, and search.
        Query params: page, per_page, status, category (slug), tag (slug), search
        Returns: { data: [...], meta: { total, totalPages, currentPage, perPage } }
        """
        from app.validation import pagination
        page, per_page = pagination()
        status    = request.args.get('status', 'published')
        category  = request.args.get('category')
        tag       = request.args.get('tag')
        search    = request.args.get('search', '').strip()

        query = BlogPost.query
        if status != 'published':
            _require_editor()

        # Status filter
        if status and status != 'all':
            query = query.filter_by(status=status)

        # Category filter by slug
        if category:
            cat = BlogCategory.query.filter_by(slug=category).first()
            if cat:
                query = query.filter_by(category_id=cat.id)
            else:
                # No matching category → return empty
                from flask import jsonify
                return {'data': [], 'meta': {'total': 0, 'totalPages': 0, 'currentPage': page, 'perPage': per_page}}

        # Tag filter by slug
        if tag:
            tag_obj = BlogTag.query.filter_by(slug=tag).first()
            if tag_obj:
                query = query.filter(BlogPost.tags.contains(tag_obj))
            else:
                return {'data': [], 'meta': {'total': 0, 'totalPages': 0, 'currentPage': page, 'perPage': per_page}}

        # Full-text search on title + excerpt + content
        if search:
            like = f'%{search}%'
            query = query.filter(
                or_(
                    BlogPost.title.ilike(like),
                    BlogPost.excerpt.ilike(like),
                    BlogPost.content.ilike(like),
                )
            )

        total = query.count()
        pages_obj = query.order_by(desc(BlogPost.created_at)) \
                         .paginate(page=page, per_page=per_page, error_out=False)

        return {
            'data': [_post_to_dict(p) for p in pages_obj.items],
            'meta': {
                'total':       total,
                'totalPages':  pages_obj.pages,
                'currentPage': page,
                'perPage':     per_page,
            }
        }

    @require_roles("admin", "super_admin")
    def post(self):
        """Create a new blog post."""
        payload = json_object()
        tag_ids = _tag_ids(payload.pop('tag_ids', None))
        data = _clean_post(payload)

        for field in ('title', 'content', 'category_id'):
            if field not in data:
                blog_ns.abort(400, f'{field} is required')
        _category_or_404(data['category_id'])

        data['author_id'] = int(get_jwt_identity())
        data['slug']      = _unique_slug(slugify(data['title']) or 'post')

        if data.get('status') == 'published':
            data['published_at'] = datetime.now(timezone.utc)

        post = BlogPost(**data)
        db.session.add(post)
        db.session.flush()
        _attach_tags(post, tag_ids)
        db.session.commit()
        return _post_to_dict(post), 201


# ── /posts/<slug> ─────────────────────────────────────────────────────────────

@blog_ns.route('/posts/<string:slug>')
@blog_ns.response(404, 'Post not found')
class BlogPostItem(Resource):

    def get(self, slug):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        if post.status != 'published':
            _require_editor()
        post.views += 1
        db.session.commit()
        return _post_to_dict(post)

    @require_roles("admin", "super_admin")
    def put(self, slug):
        return self._update(slug)

    @require_roles("admin", "super_admin")
    def patch(self, slug):
        return self._update(slug)

    def _update(self, slug):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        payload = json_object()
        tag_ids = _tag_ids(payload.pop('tag_ids')) if 'tag_ids' in payload else None
        data = _clean_post(payload)

        if 'category_id' in data:
            _category_or_404(data['category_id'])

        # Regenerate slug only if title actually changed
        if 'title' in data and data['title'] != post.title:
            data['slug'] = _unique_slug(slugify(data['title']) or 'post', exclude_id=post.id)

        new_status = data.get('status', post.status)
        if new_status == 'published' and post.published_at is None:
            post.published_at = datetime.now(timezone.utc)

        for key, value in data.items():
            setattr(post, key, value)

        if tag_ids is not None:
            _attach_tags(post, tag_ids)

        db.session.commit()
        return _post_to_dict(post)

    @require_roles("admin", "super_admin")
    def delete(self, slug):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        db.session.delete(post)
        db.session.commit()
        return '', 204


# ── /categories ───────────────────────────────────────────────────────────────

@blog_ns.route('/categories')
class BlogCategoryList(Resource):

    @blog_ns.marshal_list_with(category_model)
    def get(self):
        return BlogCategory.query.order_by(BlogCategory.name).all()

    @blog_ns.expect(category_model)
    @blog_ns.marshal_with(category_model, code=201)
    @require_roles("admin", "super_admin")
    def post(self):
        data = _clean_named(json_object(), _CATEGORY_STRINGS, _CATEGORY_IGNORED)
        if not data.get('name'):
            blog_ns.abort(400, 'name is required')
        data['slug'] = _unique_taxonomy_slug(BlogCategory, slugify(data['name']) or 'category')

        category = BlogCategory(**data)
        db.session.add(category)
        db.session.commit()
        return category, 201


@blog_ns.route('/categories/<string:slug>')
@blog_ns.response(404, 'Category not found')
class BlogCategoryItem(Resource):

    @blog_ns.marshal_with(category_model)
    def get(self, slug):
        return BlogCategory.query.filter_by(slug=slug).first_or_404()

    @blog_ns.expect(category_model)
    @blog_ns.marshal_with(category_model)
    @require_roles("admin", "super_admin")
    def put(self, slug):
        category = BlogCategory.query.filter_by(slug=slug).first_or_404()
        data = _clean_named(json_object(), _CATEGORY_STRINGS, _CATEGORY_IGNORED)
        if data.get('name') and data['name'] != category.name:
            data['slug'] = _unique_taxonomy_slug(BlogCategory, slugify(data['name']) or 'category', exclude_id=category.id)
        for key, value in data.items():
            setattr(category, key, value)
        db.session.commit()
        return category

    @blog_ns.response(204, 'Category deleted')
    @require_roles("admin", "super_admin")
    def delete(self, slug):
        category = BlogCategory.query.filter_by(slug=slug).first_or_404()
        db.session.delete(category)
        db.session.commit()
        return '', 204


# ── /tags ─────────────────────────────────────────────────────────────────────

@blog_ns.route('/tags')
class BlogTagList(Resource):

    @blog_ns.marshal_list_with(tag_model)
    def get(self):
        return BlogTag.query.order_by(BlogTag.name).all()

    @blog_ns.expect(tag_model)
    @blog_ns.marshal_with(tag_model, code=201)
    @require_roles("admin", "super_admin")
    def post(self):
        data = _clean_named(json_object(), _TAG_STRINGS, _TAG_IGNORED)
        if not data.get('name'):
            blog_ns.abort(400, 'name is required')
        data['slug'] = _unique_taxonomy_slug(BlogTag, slugify(data['name']) or 'tag')

        tag = BlogTag(**data)
        db.session.add(tag)
        db.session.commit()
        return tag, 201


@blog_ns.route('/tags/<string:slug>')
@blog_ns.response(404, 'Tag not found')
class BlogTagItem(Resource):

    @blog_ns.marshal_with(tag_model)
    def get(self, slug):
        return BlogTag.query.filter_by(slug=slug).first_or_404()

    @blog_ns.response(204, 'Tag deleted')
    @require_roles("admin", "super_admin")
    def delete(self, slug):
        tag = BlogTag.query.filter_by(slug=slug).first_or_404()
        db.session.delete(tag)
        db.session.commit()
        return '', 204


# ── /posts/<slug>/comments ────────────────────────────────────────────────────

@blog_ns.route('/posts/<string:slug>/comments')
@blog_ns.response(404, 'Post not found')
class BlogPostComments(Resource):

    def get(self, slug):
        post = BlogPost.query.filter_by(slug=slug, status='published').first_or_404()
        comments = BlogComment.query.filter_by(post_id=post.id, parent_id=None).order_by(desc(BlogComment.created_at)).all()
        return [comment.to_dict() for comment in comments]

    @blog_ns.expect(comment_model)
    @jwt_required()
    def post(self, slug):
        post = BlogPost.query.filter_by(slug=slug, status='published').first_or_404()
        from app.validation import json_object, text
        data = json_object()
        content = text(data.get('content'), 'Comment', 5000, True)
        parent_id = data.get('parent_id')
        if parent_id is not None:
            if type(parent_id) is not int:
                blog_ns.abort(400, 'Invalid parent comment')
            parent = BlogComment.query.filter_by(id=parent_id, post_id=post.id).first()
            if not parent:
                blog_ns.abort(400, 'Parent comment does not belong to this post')
            parent_id = parent.parent_id or parent.id
        comment = BlogComment(content=content, post_id=post.id, author_id=int(get_jwt_identity()), parent_id=parent_id)
        db.session.add(comment)
        db.session.commit()
        return comment.to_dict(), 201


@blog_ns.route('/posts/<string:slug>/comments/<int:comment_id>')
@blog_ns.response(404, 'Comment not found')
class BlogPostComment(Resource):

    def get(self, slug, comment_id):
        post = BlogPost.query.filter_by(slug=slug, status='published').first_or_404()
        return BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404().to_dict()

    @blog_ns.expect(comment_model)
    @jwt_required()
    def put(self, slug, comment_id):
        post    = BlogPost.query.filter_by(slug=slug, status='published').first_or_404()
        comment = BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()
        caller = db.session.get(User, int(get_jwt_identity()))
        if comment.author_id != caller.id and not caller.is_admin:
            blog_ns.abort(403, 'You may only modify your own comments')
        data    = dict(blog_ns.payload)
        content = data.get('content')
        if not isinstance(content, str) or not content.strip():
            blog_ns.abort(400, 'Content is required')
        comment.content = content.strip()
        db.session.commit()
        return comment.to_dict()

    @blog_ns.response(204, 'Comment deleted')
    @jwt_required()
    def delete(self, slug, comment_id):
        post    = BlogPost.query.filter_by(slug=slug, status='published').first_or_404()
        comment = BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()
        caller = db.session.get(User, int(get_jwt_identity()))
        if comment.author_id != caller.id and not caller.is_admin:
            blog_ns.abort(403, 'You may only modify your own comments')
        db.session.delete(comment)
        db.session.commit()
        return '', 204


def _require_editor():
    from flask_jwt_extended import verify_jwt_in_request
    verify_jwt_in_request()
    user = db.session.get(User, int(get_jwt_identity()))
    if not user.is_admin:
        blog_ns.abort(403, "Admin privileges required")
