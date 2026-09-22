from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.contact import ContactForm
from app.extensions import db
from sqlalchemy import desc

contact_ns = Namespace('contact', description='Contact form operations')

contact_model = contact_ns.model('Contact', {
    'id':         fields.Integer(readonly=True),
    'name':       fields.String(required=True),
    'email':      fields.String(required=True),
    'subject':    fields.String(),
    'message':    fields.String(required=True),
    'created_at': fields.DateTime(readonly=True),
})


@contact_ns.route('/')
class ContactList(Resource):

    @contact_ns.marshal_list_with(contact_model)
    @jwt_required()
    def get(self):
        """List all contact submissions (admin only)."""
        return ContactForm.query.order_by(desc(ContactForm.created_at)).all()

    @contact_ns.expect(contact_model)
    @contact_ns.marshal_with(contact_model, code=201)
    def post(self):
        """Submit a contact form (public)."""
        data = dict(contact_ns.payload)
        for k in ['id', 'created_at']:
            data.pop(k, None)
        contact = ContactForm(**data)
        db.session.add(contact)
        db.session.commit()
        return contact, 201


@contact_ns.route('/<int:id>')
@contact_ns.response(404, 'Not found')
class ContactItem(Resource):

    @contact_ns.marshal_with(contact_model)
    @jwt_required()
    def get(self, id):
        """Get a single contact submission."""
        return ContactForm.query.get_or_404(id)

    @contact_ns.response(204, 'Deleted')
    @jwt_required()
    def delete(self, id):
        """Delete a contact submission."""
        contact = ContactForm.query.get_or_404(id)
        db.session.delete(contact)
        db.session.commit()
        return '', 204
