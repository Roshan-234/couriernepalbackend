from datetime import datetime
from app.extensions import db
from slugify import slugify

class BlogCategory(db.Model):
    __tablename__ = "blog_categories"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    posts = db.relationship('BlogPost', backref='category', lazy=True)

    def __init__(self, **kwargs):
        super(BlogCategory, self).__init__(**kwargs)
        self.slug = slugify(self.name)

    def to_dict(self):
        """Convert the category to a dictionary representation suitable for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'posts_count': len(self.posts)
        }

class BlogPost(db.Model):
    __tablename__ = "blog_posts"
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False)
    excerpt = db.Column(db.String(500))
    featured_image = db.Column(db.String(255))
    
    # Meta fields
    status = db.Column(db.String(20), default='draft')  # draft, published
    views = db.Column(db.Integer, default=0)
    
    # Relations
    category_id = db.Column(db.Integer, db.ForeignKey('blog_categories.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = db.Column(db.DateTime)
    
    # Relationships
    author = db.relationship('User', backref='blog_posts')
    tags = db.relationship('BlogTag', secondary='post_tags', backref='posts')
    
    def __init__(self, **kwargs):
        super(BlogPost, self).__init__(**kwargs)
        self.slug = slugify(self.title)
        if not self.excerpt and self.content:
            self.excerpt = self.content[:200] + '...'

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'slug': self.slug,
            'content': self.content,
            'excerpt': self.excerpt,
            'featured_image': self.featured_image,
            'status': self.status,
            'views': self.views,
            'category': self.category.to_dict(),
            'author': {
                'id': self.author.id,
                'name': f"{self.author.first_name} {self.author.last_name}",
                'email': self.author.email
            },
            'tags': [tag.to_dict() for tag in self.tags],
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'published_at': self.published_at.isoformat() if self.published_at else None
        }

class BlogTag(db.Model):
    __tablename__ = "blog_tags"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    
    def __init__(self, **kwargs):
        super(BlogTag, self).__init__(**kwargs)
        self.slug = slugify(self.name)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug
        }

# Blog comment model
class BlogComment(db.Model):
    __tablename__ = "blog_comments"
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    post_id = db.Column(db.Integer, db.ForeignKey('blog_posts.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('blog_comments.id'), nullable=True)
    
    # Relationships
    post = db.relationship('BlogPost', backref=db.backref('comments', lazy=True))
    author = db.relationship('User', backref=db.backref('blog_comments', lazy=True))
    replies = db.relationship('BlogComment', backref=db.backref('parent', remote_side=[id]), lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'author': {
                'id': self.author.id,
                'name': f"{self.author.first_name} {self.author.last_name}",
                'email': self.author.email
            },
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'parent_id': self.parent_id,
            'replies': [reply.to_dict() for reply in self.replies] if not self.parent_id else None
        }

# Association table for posts and tags
post_tags = db.Table('post_tags',
    db.Column('post_id', db.Integer, db.ForeignKey('blog_posts.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('blog_tags.id'), primary_key=True)
)
