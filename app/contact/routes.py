from flask_restx import Namespace, Resource, fields
from app.models.contact import ContactForm
from app.extensions import db

contact_ns = Namespace('contact', description='Contact form operations')

# Models
contact_model = contact_ns.model('Contact', {
    'id': fields.Integer(readonly=True, description='Contact form ID'),
    'name': fields.String(required=True, description='Contact name'),
    'email': fields.String(required=True, description='Contact email'),
    'subject': fields.String(description='Subject'),
    'message': fields.String(required=True, description='Message content'),
    'created_at': fields.DateTime(readonly=True, description='Creation timestamp')
})

@contact_ns.route('/')
class ContactList(Resource):
    @contact_ns.doc('create_contact')
    @contact_ns.expect(contact_model)
    @contact_ns.marshal_with(contact_model, code=201)
    def post(self):
        """Submit a new contact form"""
        data = contact_ns.payload
        contact = ContactForm(**data)
        db.session.add(contact)
        db.session.commit()
        return contact, 201

@contact_ns.route('/<int:id>')
@contact_ns.response(404, 'Contact form not found')
@contact_ns.param('id', 'The contact form identifier')
class ContactItem(Resource):
    @contact_ns.doc('get_contact')
    @contact_ns.marshal_with(contact_model)
    def get(self, id):
        """Fetch a contact form by ID"""
        return ContactForm.query.get_or_404(id)
