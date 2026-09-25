from flask_restx import Namespace, Resource, fields
from app.models.contact import ContactForm
from app.extensions import db
from sqlalchemy import desc
from app.auth.utils import require_roles
from flask import request

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
    @require_roles('admin', 'super_admin')
    def get(self):
        """List all contact submissions (admin only)."""
        return ContactForm.query.order_by(desc(ContactForm.created_at)).all()

    @contact_ns.expect(contact_model, validate=True)
    @contact_ns.marshal_with(contact_model, code=201)
    def post(self):
        """Submit a contact form (public)."""
        payload = request.get_json()
        data = {key: (payload.get(key) or '').strip() for key in ('name', 'email', 'subject', 'message')}
        if any(not data[key] for key in ('name', 'email', 'message')) or '@' not in data['email']:
            contact_ns.abort(400, 'Please provide your name, a valid email and a message')
        if any(len(data[key]) > limit for key, limit in [('name', 100), ('email', 120), ('subject', 150)]) or len(data['message']) > 5000:
            contact_ns.abort(400, 'Message or contact details exceed the allowed length')
        contact = ContactForm(**data)
        db.session.add(contact)
        db.session.commit()
        return contact, 201


@contact_ns.route('/<int:id>')
@contact_ns.response(404, 'Not found')
class ContactItem(Resource):

    @contact_ns.marshal_with(contact_model)
    @require_roles('admin', 'super_admin')
    def get(self, id):
        """Get a single contact submission."""
        return ContactForm.query.get_or_404(id)

    @contact_ns.response(204, 'Deleted')
    @require_roles('admin', 'super_admin')
    def delete(self, id):
        """Delete a contact submission."""
        contact = ContactForm.query.get_or_404(id)
        db.session.delete(contact)
        db.session.commit()
        return '', 204
