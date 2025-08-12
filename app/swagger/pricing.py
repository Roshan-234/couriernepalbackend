pricing_definitions = {
    "PricingRule": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "service_type": {"type": "string"},
            "shipment_type": {"type": "string"},
            "origin_country": {"type": "string"},
            "destination_country": {"type": "string"},
            "base_price": {"type": "number", "format": "float"},
            "price_per_kg": {"type": "number", "format": "float"},
            "min_weight": {"type": "number", "format": "float"},
            "max_weight": {"type": "number", "format": "float"},
            "insurance_percentage": {"type": "number", "format": "float"},
            "tax_percentage": {"type": "number", "format": "float"},
            "is_active": {"type": "boolean"}
        }
    }
}
