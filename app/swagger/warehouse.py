warehouse_definitions = {
    "Warehouse": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "code": {"type": "string"},
            "address": {"type": "string"},
            "city": {"type": "string"},
            "state": {"type": "string"},
            "country": {"type": "string"},
            "postal_code": {"type": "string"},
            "contact_person": {"type": "string"},
            "phone": {"type": "string"},
            "email": {"type": "string", "format": "email"},
            "is_active": {"type": "boolean"}
        }
    }
}
