service_definitions = {
    "Service": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "code": {"type": "string"},
            "description": {"type": "string"},
            "icon": {"type": "string"},
            "delivery_time": {"type": "string"},
            "price_range": {"type": "string"},
            "is_active": {"type": "boolean"}
        }
    }
}
