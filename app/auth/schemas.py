from marshmallow import Schema, fields, validate

class PasswordResetRequestSchema(Schema):
    email = fields.Email(required=True)

class PasswordResetConfirmSchema(Schema):
    token    = fields.Str(required=True)
    password = fields.Str(
        required=True,
        validate=validate.Length(min=8)
        # remove description here
    )
