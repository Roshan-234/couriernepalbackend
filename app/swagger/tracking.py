tracking_definitions = {
    "TrackingEvent": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "shipment_id": {"type": "integer"},
            "location": {"type": "string"},
            "status": {"type": "string"},
            "description": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time"}
        }
    }
}
