shipment_definitions = {
    "Shipment": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "tracking_number": {"type": "string"},
            "sender_id": {"type": "integer"},
            "receiver_id": {"type": "integer"},
            "origin_address": {"type": "string"},
            "destination_address": {"type": "string"},
            "service_type": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["pending", "picked_up", "in_transit", "delivered", "cancelled"]
            },
            "estimated_delivery": {"type": "string", "format": "date-time"},
            "actual_delivery": {"type": "string", "format": "date-time"},
            "shipping_cost": {"type": "number", "format": "float"},
            "insurance_cost": {"type": "number", "format": "float"},
            "tax": {"type": "number", "format": "float"},
            "total_cost": {"type": "number", "format": "float"},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"}
        }
    }
}
