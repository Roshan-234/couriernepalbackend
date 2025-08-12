from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import jwt_required
from app.models.agent import AgentProfile
from app.extensions import db

agent_ns = Namespace('agents', description='Agent management operations')

# Models
agent_model = agent_ns.model('Agent', {
    'id': fields.Integer(readonly=True, description='Agent ID'),
    'user_id': fields.Integer(required=True, description='Associated User ID'),
    'agent_code': fields.String(required=True, description='Unique agent code'),
    'company_name': fields.String(required=True, description='Company name'),
    'address': fields.String(required=True, description='Address'),
    'city': fields.String(required=True, description='City'),
    'state': fields.String(required=True, description='State'),
    'country': fields.String(required=True, description='Country'),
    'postal_code': fields.String(required=True, description='Postal code'),
    'phone': fields.String(required=True, description='Contact phone'),
    'status': fields.String(required=True, description='Agent status', enum=['active', 'inactive', 'suspended'])
})

@agent_ns.route('/')
class AgentList(Resource):
    @agent_ns.doc('list_agents')
    @agent_ns.marshal_list_with(agent_model)
    @jwt_required()
    def get(self):
        """List all agents"""
        return AgentProfile.query.all()

    @agent_ns.doc('create_agent')
    @agent_ns.expect(agent_model)
    @agent_ns.marshal_with(agent_model, code=201)
    @jwt_required()
    def post(self):
        """Create a new agent"""
        data = agent_ns.payload
        agent = AgentProfile(**data)
        db.session.add(agent)
        db.session.commit()
        return agent, 201

@agent_ns.route('/<int:id>')
@agent_ns.response(404, 'Agent not found')
@agent_ns.param('id', 'The agent identifier')
class Agent(Resource):
    @agent_ns.doc('get_agent')
    @agent_ns.marshal_with(agent_model)
    @jwt_required()
    def get(self, id):
        """Fetch an agent by ID"""
        return AgentProfile.query.get_or_404(id)

    @agent_ns.doc('update_agent')
    @agent_ns.expect(agent_model)
    @agent_ns.marshal_with(agent_model)
    @jwt_required()
    def put(self, id):
        """Update an agent"""
        agent = AgentProfile.query.get_or_404(id)
        data = agent_ns.payload
        for key, value in data.items():
            setattr(agent, key, value)
        db.session.commit()
        return agent

    @agent_ns.doc('delete_agent')
    @agent_ns.response(204, 'Agent deleted')
    @jwt_required()
    def delete(self, id):
        """Delete an agent"""
        agent = AgentProfile.query.get_or_404(id)
        db.session.delete(agent)
        db.session.commit()
        return '', 204
