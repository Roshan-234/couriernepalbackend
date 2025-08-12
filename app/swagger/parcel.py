parcel_definitions = {
    "Parcel": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "shipment_id": {"type": "integer"},
            "tracking_number": {"type": "string"},
            "weight": {"type": "number", "format": "float"},
            "length": {"type": "number", "format": "float"},
            "width": {"type": "number", "format": "float"},
            "height": {"type": "number", "format": "float"},
            "description": {"type": "string"},
            "declared_value": {"type": "number", "format": "float"},
            "is_fragile": {"type": "boolean"},
            "special_handling": {"type": "string"}
        }
    }
}
