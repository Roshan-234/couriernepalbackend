faq_definitions = {
    "FAQ": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "question": {"type": "string"},
            "answer": {"type": "string"},
            "category": {"type": "string"},
            "order": {"type": "integer"},
            "is_active": {"type": "boolean"}
        },
        "required": ["question", "answer"]
    }
}
