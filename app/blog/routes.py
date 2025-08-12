from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user
from app.models.blog import BlogPost, BlogCategory, BlogTag, BlogComment
from app.extensions import db
from datetime import datetime
from slugify import slugify
from sqlalchemy import desc
from flask import request

blog_ns = Namespace('blog', description='Blog operations')

# Models
category_model = blog_ns.model('BlogCategory', {
    'id': fields.Integer(readonly=True, description='Category ID'),
    'name': fields.String(required=True, description='Category name'),
    'slug': fields.String(readonly=True, description='Category slug'),
    'description': fields.String(description='Category description')
})

tag_model = blog_ns.model('BlogTag', {
    'id': fields.Integer(readonly=True, description='Tag ID'),
    'name': fields.String(required=True, description='Tag name'),
    'slug': fields.String(readonly=True, description='Tag slug')
})

comment_model = blog_ns.model('BlogComment', {
    'id': fields.Integer(readonly=True, description='Comment ID'),
    'content': fields.String(required=True, description='Comment content'),
    'author_id': fields.Integer(required=True, description='Author ID'),
    'created_at': fields.DateTime(readonly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readonly=True, description='Last update timestamp'),
    'replies': fields.List(fields.Nested('self'), description='Nested replies')
})

post_model = blog_ns.model('BlogPost', {
    'id': fields.Integer(readonly=True, description='Post ID'),
    'title': fields.String(required=True, description='Post title'),
    'slug': fields.String(readonly=True, description='Post slug'),
    'content': fields.String(required=True, description='Post content'),
    'excerpt': fields.String(description='Post excerpt'),
    'featured_image': fields.String(description='Featured image URL'),
    'status': fields.String(required=True, enum=['draft', 'published'], description='Post status'),
    'views': fields.Integer(readonly=True, description='View count'),
    'author_id': fields.Integer(required=True, description='Author ID'),
    'category_id': fields.Integer(required=True, description='Category ID'),
    'tags': fields.List(fields.Nested(tag_model), description='Post tags'),
    'comments': fields.List(fields.Nested(comment_model), description='Post comments'),
    'created_at': fields.DateTime(readonly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readonly=True, description='Last update timestamp'),
    'published_at': fields.DateTime(description='Publication timestamp')
})

@blog_ns.route('/posts')
class BlogPostList(Resource):
    @blog_ns.doc('list_posts')
    @blog_ns.marshal_list_with(post_model)
    def get(self):
        """List all blog posts"""
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        status = request.args.get('status', 'published')
        
        query = BlogPost.query
        if status:
            query = query.filter_by(status=status)
        
        return query.order_by(desc(BlogPost.created_at)).paginate(page=page, per_page=per_page).items

    @blog_ns.doc('create_post')
    @blog_ns.expect(post_model)
    @blog_ns.marshal_with(post_model, code=201)
    @jwt_required()
    def post(self):
        """Create a new blog post"""
        data = blog_ns.payload
        data['slug'] = slugify(data['title'])
        post = BlogPost(**data)
        db.session.add(post)
        db.session.commit()
        return post, 201

@blog_ns.route('/posts/<string:slug>')
@blog_ns.response(404, 'Post not found')
class BlogPostItem(Resource):
    @blog_ns.doc('get_post')
    @blog_ns.marshal_with(post_model)
    def get(self, slug):
        """Get a blog post by slug"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        post.views += 1
        db.session.commit()
        return post

    @blog_ns.doc('update_post')
    @blog_ns.expect(post_model)
    @blog_ns.marshal_with(post_model)
    @jwt_required()
    def put(self, slug):
        """Update a blog post"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        data = blog_ns.payload
        data['slug'] = slugify(data['title'])
        for key, value in data.items():
            if key == 'tags':
                post.tags = []
                for tag_data in value:
                    tag = BlogTag.query.get(tag_data['id'])
                    if tag:
                        post.tags.append(tag)
            else:
                setattr(post, key, value)
        db.session.commit()
        return post

    @blog_ns.doc('delete_post')
    @blog_ns.response(204, 'Post deleted')
    @jwt_required()
    def delete(self, slug):
        """Delete a blog post"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        db.session.delete(post)
        db.session.commit()
        return '', 204

@blog_ns.route('/categories')
class BlogCategoryList(Resource):
    @blog_ns.doc('list_categories')
    @blog_ns.marshal_list_with(category_model)
    def get(self):
        """List all blog categories"""
        return BlogCategory.query.all()

    @blog_ns.doc('create_category')
    @blog_ns.expect(category_model)
    @blog_ns.marshal_with(category_model, code=201)
    @jwt_required()
    def post(self):
        """Create a new blog category"""
        data = blog_ns.payload
        data['slug'] = slugify(data['name'])
        category = BlogCategory(**data)
        db.session.add(category)
        db.session.commit()
        return category, 201

@blog_ns.route('/categories/<string:slug>')
@blog_ns.response(404, 'Category not found')
class BlogCategoryItem(Resource):
    @blog_ns.doc('get_category')
    @blog_ns.marshal_with(category_model)
    def get(self, slug):
        """Get a blog category by slug"""
        return BlogCategory.query.filter_by(slug=slug).first_or_404()

    @blog_ns.doc('update_category')
    @blog_ns.expect(category_model)
    @blog_ns.marshal_with(category_model)
    @jwt_required()
    def put(self, slug):
        """Update a blog category"""
        category = BlogCategory.query.filter_by(slug=slug).first_or_404()
        data = blog_ns.payload
        data['slug'] = slugify(data['name'])
        for key, value in data.items():
            setattr(category, key, value)
        db.session.commit()
        return category

    @blog_ns.doc('delete_category')
    @blog_ns.response(204, 'Category deleted')
    @jwt_required()
    def delete(self, slug):
        """Delete a blog category"""
        category = BlogCategory.query.filter_by(slug=slug).first_or_404()
        db.session.delete(category)
        db.session.commit()
        return '', 204

@blog_ns.route('/tags')
class BlogTagList(Resource):
    @blog_ns.doc('list_tags')
    @blog_ns.marshal_list_with(tag_model)
    def get(self):
        """List all blog tags"""
        return BlogTag.query.all()

    @blog_ns.doc('create_tag')
    @blog_ns.expect(tag_model)
    @blog_ns.marshal_with(tag_model, code=201)
    @jwt_required()
    def post(self):
        """Create a new blog tag"""
        data = blog_ns.payload
        data['slug'] = slugify(data['name'])
        tag = BlogTag(**data)
        db.session.add(tag)
        db.session.commit()
        return tag, 201

@blog_ns.route('/posts/<string:slug>/comments')
@blog_ns.response(404, 'Post not found')
class BlogPostComments(Resource):
    @blog_ns.doc('list_comments')
    @blog_ns.marshal_list_with(comment_model)
    def get(self, slug):
        """List all comments for a post"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        return BlogComment.query.filter_by(post_id=post.id).order_by(desc(BlogComment.created_at)).all()

    @blog_ns.doc('create_comment')
    @blog_ns.expect(comment_model)
    @blog_ns.marshal_with(comment_model, code=201)
    @jwt_required()
    def post(self, slug):
        """Create a new comment on a post"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        data = blog_ns.payload
        data['post_id'] = post.id
        data['author_id'] = get_jwt_identity()
        comment = BlogComment(**data)
        db.session.add(comment)
        db.session.commit()
        return comment, 201

@blog_ns.route('/posts/<string:slug>/comments/<int:comment_id>')
@blog_ns.response(404, 'Comment not found')
class BlogPostComment(Resource):
    @blog_ns.doc('get_comment')
    @blog_ns.marshal_with(comment_model)
    def get(self, slug, comment_id):
        """Get a comment by ID"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        return BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()

    @blog_ns.doc('update_comment')
    @blog_ns.expect(comment_model)
    @blog_ns.marshal_with(comment_model)
    @jwt_required()
    def put(self, slug, comment_id):
        """Update a comment"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        comment = BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()
        data = blog_ns.payload
        for key, value in data.items():
            setattr(comment, key, value)
        db.session.commit()
        return comment

    @blog_ns.doc('delete_comment')
    @blog_ns.response(204, 'Comment deleted')
    @jwt_required()
    def delete(self, slug, comment_id):
        """Delete a comment"""
        post = BlogPost.query.filter_by(slug=slug).first_or_404()
        comment = BlogComment.query.filter_by(post_id=post.id, id=comment_id).first_or_404()
        db.session.delete(comment)
        db.session.commit()
        return '', 204
