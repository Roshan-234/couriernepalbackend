agent_definitions = {
    "Agent": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "user_id": {"type": "integer"},
            "agent_code": {"type": "string"},
            "company_name": {"type": "string"},
            "address": {"type": "string"},
            "city": {"type": "string"},
            "state": {"type": "string"},
            "country": {"type": "string"},
            "postal_code": {"type": "string"},
            "phone": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["active", "inactive", "suspended"]
            }
        }
    }
}
