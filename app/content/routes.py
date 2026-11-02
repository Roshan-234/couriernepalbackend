from flask import Blueprint, request
from sqlalchemy import update
from app.extensions import db
from app.models.site_page import SitePage
from app.auth.utils import require_roles
from app.validation import json_object, text
from werkzeug.exceptions import BadRequest

content_bp = Blueprint('content', __name__)


@content_bp.get('/pages')
def pages():
    return {'pages': [page.to_dict() for page in SitePage.query.filter_by(published=True).order_by(SitePage.path).all()]}


@content_bp.get('/page')
def page():
    return SitePage.query.filter_by(path=request.args.get('path', '/'), published=True).first_or_404().to_dict()


@content_bp.get('/article-index')
def article_index():
    from app.models.blog import BlogPost
    rows = db.session.query(BlogPost.slug, BlogPost.updated_at).filter_by(status='published').order_by(BlogPost.id).limit(10000).all()
    return {'articles': [{'slug': slug, 'updated_at': updated.isoformat() if updated else None} for slug, updated in rows]}


@content_bp.get('/admin/pages')
@require_roles('admin', 'super_admin')
def admin_pages():
    return {'pages': [page.to_dict() for page in SitePage.query.order_by(SitePage.path).all()]}


@content_bp.put('/admin/page')
@require_roles('admin', 'super_admin')
def update_page():
    data = json_object()
    page = SitePage.query.filter_by(path=request.args.get('path')).first_or_404()
    allowed = {'title', 'description', 'eyebrow', 'heading', 'intro', 'sections', 'published', 'version'}
    if set(data) - allowed:
        raise BadRequest('Unknown content field; page paths are immutable')
    if type(data.get('version')) is not int:
        raise BadRequest('A version number is required')
    values = {}
    for key, limit in [('title', 160), ('description', 320), ('eyebrow', 160), ('heading', 200), ('intro', 5000)]:
        values[key] = text(data.get(key), key, limit, key in ('title', 'description', 'heading'))
    sections = data.get('sections')
    if not isinstance(sections, list) or len(sections) > 20:
        raise BadRequest('Provide at most 20 sections')
    cleaned = []
    for section in sections:
        if not isinstance(section, dict) or set(section) - {'heading', 'body'}:
            raise BadRequest('Sections contain only heading and body')
        cleaned.append({'heading': text(section.get('heading'), 'Section heading', 200, True),
                        'body': text(section.get('body'), 'Section text', 5000, True)})
    if type(data.get('published')) is not bool:
        raise BadRequest('Published must be true or false')
    if page.path == '/' and not data['published']:
        raise BadRequest('The homepage must remain published')
    from datetime import datetime
    result = db.session.execute(update(SitePage).where(SitePage.id == page.id, SitePage.version == data['version']).values(
        **values, sections=cleaned, published=data['published'], version=SitePage.version + 1, updated_at=datetime.utcnow()))
    if result.rowcount != 1:
        db.session.rollback()
        return {'msg': 'Another editor saved this page. Reload before saving again.'}, 409
    db.session.commit()
    db.session.refresh(page)
    return page.to_dict()
