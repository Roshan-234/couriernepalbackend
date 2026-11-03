"""Branch and hub network"""
from flask import request
from flask_restx import Namespace, Resource, fields
from app.auth.utils import require_roles
from app.data import nepal_geo
from app.extensions import db
from app.models.branch import Branch
from app.validation import json_object, text

branch_ns = Namespace('branches', description='Branch and hub network')

branch_in = branch_ns.model('BranchIn', {
    'code':             fields.String(required=True),
    'name':             fields.String(required=True),
    'type':             fields.String(description=', '.join(Branch.TYPES)),
    'district':         fields.String(required=True),
    'province':         fields.String(),
    'city':             fields.String(),
    'address':          fields.String(),
    'landmark':         fields.String(),
    'postal_code':      fields.String(),
    'latitude':         fields.Float(),
    'longitude':        fields.Float(),
    'phone':            fields.String(),
    'alternate_phone':  fields.String(),
    'email':            fields.String(),
    'manager_name':     fields.String(),
    'opening_hours':    fields.String(),
    'serves_districts': fields.List(fields.String()),
    'parent_branch_id': fields.Integer(),
    'accepts_walk_in':  fields.Boolean(),
    'handles_cod':      fields.Boolean(),
    'handles_international': fields.Boolean(),
})

#: Columns an administrator may write, with their text length budget.
_TEXT_FIELDS = {
    'name': 150, 'city': 100, 'address': 2000, 'landmark': 255,
    'postal_code': 20, 'phone': 50, 'alternate_phone': 50, 'email': 200,
    'manager_name': 150, 'opening_hours': 255,
}
_FLAG_FIELDS = ('accepts_walk_in', 'handles_cod', 'handles_international')
_COORD_FIELDS = ('latitude', 'longitude')


def _clean_district(value, required=True):
    """Resolve a district against nepal_geo, rejecting anything unknown."""
    if value is None and not required:
        return None
    found = nepal_geo.district(value if isinstance(value, str) else '')
    if not found:
        branch_ns.abort(400, 'Unknown district')
    return found


def _clean_serves(value):
    if not isinstance(value, list) or len(value) > 77:
        branch_ns.abort(400, 'serves_districts must be a list of at most 77 districts')
    names = []
    for entry in value:
        found = nepal_geo.district(entry if isinstance(entry, str) else '')
        if not found:
            branch_ns.abort(400, f'Unknown district in serves_districts: {entry}')
        names.append(found['name'])
    return names


def _apply(branch, data, creating=False):
    """Copy whitelisted fields onto a branch. Never mass-assign."""
    for key, limit in _TEXT_FIELDS.items():
        if key in data or (creating and key == 'name'):
            setattr(branch, key, text(data.get(key), key, limit, required=(key == 'name')))
    if 'type' in data:
        if data['type'] not in Branch.TYPES:
            branch_ns.abort(400, f"type must be one of {', '.join(Branch.TYPES)}")
        branch.type = data['type']
    if 'district' in data or creating:
        found = _clean_district(data.get('district'))
        branch.district = found['name']
        # Province follows the district unless the caller overrides it.
        branch.province = found['province']
    if data.get('province'):
        branch.province = text(data['province'], 'province', 60)
    if 'serves_districts' in data:
        branch.serves_districts = _clean_serves(data['serves_districts'])
    for key in _FLAG_FIELDS:
        if key in data:
            setattr(branch, key, bool(data[key]))
    for key in _COORD_FIELDS:
        if data.get(key) is not None:
            if isinstance(data[key], bool) or not isinstance(data[key], (int, float)):
                branch_ns.abort(400, f'{key} must be a number')
            setattr(branch, key, float(data[key]))
    if 'parent_branch_id' in data and data['parent_branch_id'] is not None:
        parent = db.session.get(Branch, data['parent_branch_id']) if isinstance(data['parent_branch_id'], int) else None
        if not parent:
            branch_ns.abort(400, 'parent_branch_id does not exist')
        if parent.id == branch.id:
            branch_ns.abort(400, 'A branch cannot be its own parent')
        branch.parent_branch_id = parent.id
    return branch


def _lookup(ident):
    """Branches are addressed by numeric id or by their public code."""
    branch = None
    if ident.isdigit():
        branch = db.session.get(Branch, int(ident))
    if not branch:
        branch = Branch.query.filter_by(code=ident).first()
    if not branch:
        branch_ns.abort(404, 'Branch not found')
    return branch


@branch_ns.route('/')
class BranchList(Resource):
    @branch_ns.doc('list_branches')
    @branch_ns.param('district', 'Filter by district')
    @branch_ns.param('type', 'Filter by branch type')
    def get(self):
        """List active branches (public)."""
        query = Branch.query.filter_by(is_active=True)
        if request.args.get('district'):
            found = nepal_geo.district(request.args['district'])
            query = query.filter(Branch.district == (found['name'] if found else request.args['district']))
        if request.args.get('type'):
            query = query.filter(Branch.type == request.args['type'])
        if request.args.get('province'):
            query = query.filter(Branch.province == request.args['province'])
        branches = query.order_by(Branch.name.asc()).all()
        return {"items": [b.to_dict() for b in branches], "total": len(branches)}

    @branch_ns.doc('create_branch')
    @branch_ns.expect(branch_in)
    @branch_ns.response(201, 'Branch created')
    @branch_ns.response(409, 'Branch code already exists')
    @require_roles('admin', 'super_admin')
    def post(self):
        """Create a branch (admin)."""
        data = json_object()
        code = text(data.get('code'), 'code', 30, required=True).upper()
        if Branch.query.filter_by(code=code).first():
            branch_ns.abort(409, 'Branch code already exists')
        branch = Branch(code=code)
        _apply(branch, data, creating=True)
        db.session.add(branch)
        db.session.commit()
        return branch.to_dict(), 201


@branch_ns.route('/<string:ident>')
@branch_ns.response(404, 'Branch not found')
@branch_ns.param('ident', 'Branch id or branch code')
class BranchItem(Resource):
    @branch_ns.doc('get_branch')
    def get(self, ident):
        """Fetch a branch by id or code (public)."""
        return _lookup(ident).to_dict()

    @branch_ns.doc('update_branch')
    @branch_ns.expect(branch_in)
    @require_roles('admin', 'super_admin')
    def put(self, ident):
        """Update a branch (admin)."""
        branch = _lookup(ident)
        _apply(branch, json_object())
        db.session.commit()
        return branch.to_dict()

    @branch_ns.doc('deactivate_branch')
    @require_roles('admin', 'super_admin')
    def delete(self, ident):
        """Deactivate a branch (admin). Branches are never hard deleted —
        shipments and riders keep pointing at them."""
        branch = _lookup(ident)
        branch.is_active = False
        db.session.commit()
        return {"msg": "Branch deactivated", "code": branch.code, "is_active": branch.is_active}


@branch_ns.route('/serving/<string:district>')
class BranchesServingDistrict(Resource):
    @branch_ns.doc('branches_serving_district')
    def get(self, district):
        """List active branches that deliver to a district (public)."""
        found = nepal_geo.district(district)
        if not found:
            branch_ns.abort(404, 'Unknown district')
        branches = [b for b in Branch.query.filter_by(is_active=True).all() if b.serves(found['name'])]
        return {"district": found, "items": [b.to_dict() for b in branches], "total": len(branches)}
