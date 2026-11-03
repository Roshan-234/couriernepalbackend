from flask_restx import Namespace, Resource, fields
from app.auth.utils import require_roles
from app.models.agent import AgentProfile
from app.models.user import User
from app.validation import json_object, text
from app.extensions import db

agent_ns = Namespace('agents', description='Agent management operations')

# Models
#
# This model used to advertise agent_code, city, state, country, postal_code
# and status, none of which exist on AgentProfile: a client that believed the
# Swagger contract got a TypeError and a 500. It now mirrors the columns the
# model actually has.
agent_model = agent_ns.model('Agent', {
    'id': fields.Integer(readonly=True, description='Agent ID'),
    'user_id': fields.Integer(required=True, description='Associated User ID (set once, at creation)'),
    'company_name': fields.String(description='Company name'),
    'phone': fields.String(description='Contact phone'),
    'address': fields.String(description='Address'),
    'onboarded_at': fields.DateTime(readonly=True, description='When the agent was onboarded'),
})

# ── Writable field whitelist ─────────────────────────────────────────────────
#
# user_id is the link between a profile and the account that owns it. It is
# accepted at creation and frozen afterwards: repointing it would hand one
# agent's shipments to another account.

_STRING_FIELDS = {'company_name': 100, 'phone': 20, 'address': 255}

#: Server-owned or relationship keys. Dropped rather than refused so a
#: round-tripped row still saves, but never written.
_IGNORED_FIELDS = ('id', 'onboarded_at', 'user', 'shipments')


def _clean_agent(data, existing=None):
    """Validate a payload and return only the columns a client may write."""
    unknown = set(data) - set(_STRING_FIELDS) - {'user_id'} - set(_IGNORED_FIELDS)
    if unknown:
        agent_ns.abort(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    clean = {}
    for field, limit in _STRING_FIELDS.items():
        if field in data:
            value = data[field]
            clean[field] = None if value is None else text(value, field, limit)

    if 'user_id' in data:
        user_id = data['user_id']
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            agent_ns.abort(400, "'user_id' must be an integer")
        if existing is not None:
            # An update may echo the current owner back, but never change it.
            if user_id != existing.user_id:
                agent_ns.abort(400, "'user_id' cannot be changed once the profile exists")
        else:
            if not db.session.get(User, user_id):
                agent_ns.abort(400, f"User {user_id} does not exist")
            if AgentProfile.query.filter_by(user_id=user_id).first():
                agent_ns.abort(409, f"User {user_id} already has an agent profile")
            clean['user_id'] = user_id
    return clean


@agent_ns.route('/')
class AgentList(Resource):
    @agent_ns.doc('list_agents')
    @agent_ns.marshal_list_with(agent_model)
    @require_roles("admin", "super_admin")
    def get(self):
        """List all agents"""
        return AgentProfile.query.all()

    @agent_ns.doc('create_agent')
    @agent_ns.expect(agent_model)
    @agent_ns.marshal_with(agent_model, code=201)
    @require_roles("admin", "super_admin")
    def post(self):
        """Create a new agent"""
        data = _clean_agent(json_object())
        if 'user_id' not in data:
            agent_ns.abort(400, "'user_id' is required")
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
    @require_roles("admin", "super_admin")
    def get(self, id):
        """Fetch an agent by ID"""
        agent = db.session.get(AgentProfile, id)
        if not agent:
            agent_ns.abort(404, 'Agent not found')
        return agent

    @agent_ns.doc('update_agent')
    @agent_ns.expect(agent_model)
    @agent_ns.marshal_with(agent_model)
    @require_roles("admin", "super_admin")
    def put(self, id):
        """Update an agent"""
        agent = db.session.get(AgentProfile, id)
        if not agent:
            agent_ns.abort(404, 'Agent not found')
        for key, value in _clean_agent(json_object(), existing=agent).items():
            setattr(agent, key, value)
        db.session.commit()
        return agent

    @agent_ns.doc('delete_agent')
    @agent_ns.response(204, 'Agent deleted')
    @require_roles("admin", "super_admin")
    def delete(self, id):
        """Delete an agent"""
        agent = db.session.get(AgentProfile, id)
        if not agent:
            agent_ns.abort(404, 'Agent not found')
        db.session.delete(agent)
        db.session.commit()
        return '', 204
