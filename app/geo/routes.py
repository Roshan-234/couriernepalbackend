"""Nepal districts, provinces and delivery zones"""
from flask import request
from flask_restx import Namespace, Resource
from app.data import nepal_geo

geo_ns = Namespace('geo', description='Nepal districts, provinces and delivery zones')


@geo_ns.route('/provinces')
class ProvinceList(Resource):
    @geo_ns.doc('list_provinces')
    def get(self):
        """List the seven provinces."""
        return {
            "items": [
                {
                    "name": name,
                    "code": meta["code"],
                    "capital": meta["capital"],
                    "district_count": len(meta["districts"]),
                }
                for name, meta in nepal_geo.PROVINCES.items()
            ],
            "total": len(nepal_geo.PROVINCES),
        }


@geo_ns.route('/districts')
class DistrictList(Resource):
    @geo_ns.doc('list_districts')
    @geo_ns.param('province', 'Filter by province name')
    @geo_ns.param('zone', 'Filter by delivery zone code')
    def get(self):
        """List districts, optionally filtered by province or delivery zone."""
        province = (request.args.get('province') or '').strip().lower()
        zone = (request.args.get('zone') or '').strip().lower()
        items = [
            district for district in nepal_geo.DISTRICTS
            if (not province or district["province"].lower() == province)
            and (not zone or district["zone"] == zone)
        ]
        return {"items": items, "total": len(items)}


@geo_ns.route('/districts/<string:slug>')
@geo_ns.response(404, 'District not found')
class DistrictItem(Resource):
    @geo_ns.doc('get_district')
    def get(self, slug):
        """Fetch one district by slug (or name)."""
        found = nepal_geo.district(slug)
        if not found:
            geo_ns.abort(404, 'District not found')
        return found


@geo_ns.route('/zones')
class ZoneList(Resource):
    @geo_ns.doc('list_zones')
    def get(self):
        """List delivery zones and their service expectations."""
        items = [{"code": code, "label": label, "eta": eta}
                 for code, label, eta in nepal_geo.ZONES]
        return {"items": items, "total": len(items)}
