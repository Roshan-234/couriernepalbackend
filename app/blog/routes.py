from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models.blog import BlogPost, BlogCategory, BlogTag, BlogComment
from app.extensions import db
from datetime import datetime, timezone
from slugify import slugify
from sqlalchemy import desc, or_
from flask import request

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
        tag = BlogTag.query.get(tid)
        if tag:
            post.tags.append(tag)


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
        'author': {
            'id':       author.id,
            'name':     f"{author.first_name} {author.last_name}".strip() if author else 'Unknown',
            'username': author.email if author else '',
            'email':    author.email if author else '',
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
        page      = int(request.args.get('page', 1))
        per_page  = int(request.args.get('per_page', 10))
        status    = request.args.get('status', 'published')
        category  = request.args.get('category')
        tag       = request.args.get('tag')
        search    = request.args.get('search', '').strip()

        query = BlogPost.query

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

    @jwt_required()
    def post(self):
        """Create a new blog post."""
        data = dict(request.get_json(force=True))

        # Validate
        if not data.get('title', '').strip():
            blog_ns.abort(400, 'title is required')
        if not data.get('content', '').strip():
            blog_ns.abort(400, 'content is required')
        if not data.get('category_id'):
            blog_ns.abort(400, 'category_id is required')

        category = BlogCategory.query.get(data['category_id'])
        if not category:
            blog_ns.abort(404, f"Category {data['category_id']} not found")

        tag_ids = data.pop('tag_ids', []) or []
        # Strip non-model keys
        for k in ['tags', 'id', 'slug', 'views', 'created_at', 'updated_at',
                   'published_at', 'author_id', 'author', 'category']:
            data.pop(k, None)

        data['author_id'] = int(get_jwt_identity())
        data['slug']      = _unique_slug(slugify(data['title']))

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
        post.views += 1
        db.session.commit()
        return _post_to_dict(post)

    @jwt_required()
    def put(self, slug):
        return self._update(slug)

    @jwt_required()
    def patch(self, slug):
        return self._update(slug)

    def _update(self, slug):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        data = dict(request.get_json(force=True))

        tag_ids = data.pop('tag_ids', None)
        for k in ['tags', 'id', 'views', 'created_at', 'updated_at',
                   'published_at', 'author_id', 'author', 'category']:
            data.pop(k, None)

        # Regenerate slug only if title actually changed
        if 'title' in data and data['title'].strip() != post.title:
            data['slug'] = _unique_slug(slugify(data['title']), exclude_id=post.id)
        else:
            data.pop('slug', None)

        new_status = data.get('status', post.status)
        if new_status == 'published' and post.published_at is None:
            post.published_at = datetime.now(timezone.utc)

        for key, value in data.items():
            setattr(post, key, value)

        if tag_ids is not None:
            _attach_tags(post, tag_ids)

        db.session.commit()
        return _post_to_dict(post)

    @jwt_required()
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
    @jwt_required()
    def post(self):
        data = dict(blog_ns.payload)
        if not data.get('name', '').strip():
            blog_ns.abort(400, 'name is required')

        base_slug = slugify(data['name'])
        slug, n = base_slug, 1
        while BlogCategory.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{n}"; n += 1

        for k in ['id', 'slug', 'created_at']:
            data.pop(k, None)
        data['slug'] = slug

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
    @jwt_required()
    def put(self, slug):
        category = BlogCategory.query.filter_by(slug=slug).first_or_404()
        data = dict(blog_ns.payload)
        data['slug'] = slugify(data['name'])
        for key, value in data.items():
            setattr(category, key, value)
        db.session.commit()
        return category

    @blog_ns.response(204, 'Category deleted')
    @jwt_required()
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
    @jwt_required()
    def post(self):
        data = dict(blog_ns.payload)
        if not data.get('name', '').strip():
            blog_ns.abort(400, 'name is required')

        base_slug = slugify(data['name'])
        slug, n = base_slug, 1
        while BlogTag.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{n}"; n += 1

        for k in ['id', 'slug']:
            data.pop(k, None)
        data['slug'] = slug

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
    @jwt_required()
    def delete(self, slug):
        tag = BlogTag.query.filter_by(slug=slug).first_or_404()
        db.session.delete(tag)
        db.session.commit()
        return '', 204


# ── /posts/<slug>/comments ────────────────────────────────────────────────────

@blog_ns.route('/posts/<string:slug>/comments')
@blog_ns.response(404, 'Post not found')
class BlogPostComments(Resource):

    @blog_ns.marshal_list_with(comment_model)
    def get(self, slug):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        return BlogComment.query.filter_by(post_id=post.id, parent_id=None) \
                                .order_by(desc(BlogComment.created_at)).all()

    @blog_ns.expect(comment_model)
    @blog_ns.marshal_with(comment_model, code=201)
    @jwt_required()
    def post(self, slug):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        data = dict(blog_ns.payload)
        data['post_id']   = post.id
        data['author_id'] = int(get_jwt_identity())
        for k in ['id', 'created_at', 'updated_at']:
            data.pop(k, None)
        comment = BlogComment(**data)
        db.session.add(comment)
        db.session.commit()
        return comment, 201


@blog_ns.route('/posts/<string:slug>/comments/<int:comment_id>')
@blog_ns.response(404, 'Comment not found')
class BlogPostComment(Resource):

    @blog_ns.marshal_with(comment_model)
    def get(self, slug, comment_id):
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        return BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()

    @blog_ns.expect(comment_model)
    @blog_ns.marshal_with(comment_model)
    @jwt_required()
    def put(self, slug, comment_id):
        post    = BlogPost.query.filter_by(slug=slug).first_or_404()
        comment = BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()
        data    = dict(blog_ns.payload)
        data.pop('author_id', None)
        for key, value in data.items():
            setattr(comment, key, value)
        db.session.commit()
        return comment

    @blog_ns.response(204, 'Comment deleted')
    @jwt_required()
    def delete(self, slug, comment_id):
        post    = BlogPost.query.filter_by(slug=slug).first_or_404()
        comment = BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()
        db.session.delete(comment)
        db.session.commit()
        return '', 204
