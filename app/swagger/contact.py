contact_definitions = {
    "ContactForm": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "email": {"type": "string", "format": "email"},
            "subject": {"type": "string"},
            "message": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"}
        },
        "required": ["name", "email", "message"]
    }
}
