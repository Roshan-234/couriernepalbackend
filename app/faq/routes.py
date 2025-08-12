from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.faq import FAQ
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
    @jwt_required()
    def post(self):
        """Create a new FAQ"""
        data = faq_ns.payload
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
        return FAQ.query.get_or_404(id)

    @faq_ns.doc('update_faq')
    @faq_ns.expect(faq_model)
    @faq_ns.marshal_with(faq_model)
    @jwt_required()
    def put(self, id):
        """Update a FAQ"""
        faq = FAQ.query.get_or_404(id)
        data = faq_ns.payload
        for key, value in data.items():
            setattr(faq, key, value)
        db.session.commit()
        return faq

    @faq_ns.doc('delete_faq')
    @faq_ns.response(204, 'FAQ deleted')
    @jwt_required()
    def delete(self, id):
        """Delete a FAQ"""
        faq = FAQ.query.get_or_404(id)
        db.session.delete(faq)
        db.session.commit()
        return '', 204
