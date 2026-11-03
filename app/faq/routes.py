from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from app.auth.utils import require_roles
from app.models.faq import FAQ
from app.models.user import User
from app.validation import json_object, text
from app.extensions import db

faq_ns = Namespace('faq', description='FAQ operations')

# Models
faq_model = faq_ns.model('FAQ', {
    'id': fields.Integer(readonly=True, description='FAQ ID'),
    'question': fields.String(required=True, description='Question'),
    'answer': fields.String(required=True, description='Answer'),
    'category': fields.String(required=True, description='Category'),
    'order': fields.Integer(description='Display order'),
    'is_active': fields.Boolean(default=True, description='Is active')
})

# ── Writable field whitelist ─────────────────────────────────────────────────
#
# ``FAQ(**payload)`` and a blind setattr loop accept any attribute name, so a
# caller could set 'id' or raise a 500 with a typo. Every writable field is
# named here with the bound it is checked against.

_STRING_FIELDS = {'question': 500, 'answer': 20000, 'category': 100}

_BOOL_FIELDS = ('is_active',)

#: ``order`` is a display rank, so it is bounded rather than left unbounded.
_INT_FIELDS = {'order': (0, 100000)}

_REQUIRED_FIELDS = ('question', 'answer')

#: Server-owned keys. Dropped rather than refused so a round-tripped row still
#: saves, but never written.
_IGNORED_FIELDS = ('id', 'created_at')


def _clean_faq(data):
    """Validate a payload and return only the columns a client may write."""
    allowed = set(_STRING_FIELDS) | set(_BOOL_FIELDS) | set(_INT_FIELDS) | set(_IGNORED_FIELDS)
    unknown = set(data) - allowed
    if unknown:
        faq_ns.abort(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    clean = {}
    for field, limit in _STRING_FIELDS.items():
        if field in data:
            value, required = data[field], field in _REQUIRED_FIELDS
            clean[field] = None if value is None and not required else text(value, field, limit, required)
    for field in _BOOL_FIELDS:
        if field in data:
            if not isinstance(data[field], bool):
                faq_ns.abort(400, f"'{field}' must be true or false")
            clean[field] = data[field]
    for field, (low, high) in _INT_FIELDS.items():
        if field in data:
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                faq_ns.abort(400, f"'{field}' must be an integer between {low} and {high}")
            clean[field] = value
    return clean


def _caller_is_admin():
    """True when the request carries a valid admin token; anonymous is False."""
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
    except Exception:
        # This endpoint is public, so a missing or unusable token simply means
        # the caller is not an administrator.
        return False
    if identity is None:
        return False
    user = db.session.get(User, int(identity))
    return bool(user and user.is_admin)


def _visible_or_404(faq):
    """A deactivated FAQ is invisible to everyone but an administrator."""
    if faq is None or (not faq.is_active and not _caller_is_admin()):
        faq_ns.abort(404, 'FAQ not found')
    return faq


@faq_ns.route('/')
class FAQList(Resource):
    @faq_ns.doc('list_faqs')
    @faq_ns.marshal_list_with(faq_model)
    def get(self):
        """List all FAQs"""
        return FAQ.query.filter_by(is_active=True).order_by(FAQ.order).all()

    @faq_ns.doc('create_faq')
    @faq_ns.expect(faq_model)
    @faq_ns.marshal_with(faq_model, code=201)
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a new FAQ"""
        data = _clean_faq(json_object())
        for field in _REQUIRED_FIELDS:
            if not data.get(field):
                faq_ns.abort(400, f"'{field}' is required")
        faq = FAQ(**data)
        db.session.add(faq)
        db.session.commit()
        return faq, 201


@faq_ns.route('/<int:id>')
@faq_ns.response(404, 'FAQ not found')
@faq_ns.param('id', 'The FAQ identifier')
class FAQItem(Resource):
    @faq_ns.doc('get_faq')
    @faq_ns.marshal_with(faq_model)
    def get(self, id):
        """Fetch a FAQ by ID"""
        return _visible_or_404(db.session.get(FAQ, id))

    @faq_ns.doc('update_faq')
    @faq_ns.expect(faq_model)
    @faq_ns.marshal_with(faq_model)
    @require_roles("admin", "super_admin")
    def put(self, id):
        """Update a FAQ"""
        faq = db.session.get(FAQ, id)
        if not faq:
            faq_ns.abort(404, 'FAQ not found')
        for key, value in _clean_faq(json_object()).items():
            setattr(faq, key, value)
        db.session.commit()
        return faq

    @faq_ns.doc('delete_faq')
    @faq_ns.response(204, 'FAQ deleted')
    @require_roles("admin", "super_admin")
    def delete(self, id):
        """Delete a FAQ"""
        faq = db.session.get(FAQ, id)
        if not faq:
            faq_ns.abort(404, 'FAQ not found')
        db.session.delete(faq)
        db.session.commit()
        return '', 204
