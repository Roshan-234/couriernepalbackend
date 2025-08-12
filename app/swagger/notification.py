notification_definitions = {
    "NotificationLog": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "user_id": {"type": "integer"},
            "channel": {
                "type": "string",
                "enum": ["EMAIL", "SMS"]
            },
            "payload": {"type": "string"},
            "sent_at": {"type": "string", "format": "date-time"},
            "status": {"type": "string"}
        }
    }
}
