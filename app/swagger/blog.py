"""
Swagger documentation definitions for the blog API.
"""

blog_definitions = {
    "BlogPost": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "string"},
            "slug": {"type": "string"},
            "content": {"type": "string"},
            "excerpt": {"type": "string"},
            "featured_image": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["draft", "published"]
            },
            "views": {"type": "integer"},
            "category": {"$ref": "#/definitions/BlogCategory"},
            "author": {"$ref": "#/definitions/BlogAuthor"},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "published_at": {"type": "string", "format": "date-time"}
        }
    },
    "BlogCategory": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "slug": {"type": "string"},
            "description": {"type": "string"}
        }
    },
    "BlogComment": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "content": {"type": "string"},
            "author": {"$ref": "#/definitions/BlogAuthor"},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "replies": {
                "type": "array",
                "items": {"$ref": "#/definitions/BlogComment"}
            }
        }
    },
    "BlogAuthor": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "email": {"type": "string"},
            "avatar": {"type": "string"}
        }
    }
}
