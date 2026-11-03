"""Encrypt tracking provider api key at rest

Renames ``tracking_providers.api_key`` to ``api_key_encrypted`` and widens it
to TEXT, because a Fernet token is longer than the plaintext key it replaces.

No data migration runs here. ``app.crypto.decrypt`` passes through a value
that carries no ``enc:v1:`` prefix, so rows written before encryption existed
keep reading correctly and are re-encrypted the next time they are saved.

Revision ID: 378a9bad4dbc
Revises: f76d0cba22f2
Create Date: 2026-11-03 13:02:39.252128

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '378a9bad4dbc'
down_revision = 'f76d0cba22f2'
branch_labels = None
depends_on = None

# SQLite cannot alter a column in place, so batch mode copies the table. The
# copy re-emits every reflected constraint, and the original CREATE TABLE
# declared UNIQUE (code) without a name, which batch mode refuses to render.
# Naming it here gives the copy something to emit.
NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade():
    with op.batch_alter_table('tracking_providers', schema=None,
                              naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.alter_column('api_key',
                              new_column_name='api_key_encrypted',
                              existing_type=sa.VARCHAR(length=255),
                              type_=sa.Text(),
                              existing_nullable=True)


def downgrade():
    with op.batch_alter_table('tracking_providers', schema=None,
                              naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.alter_column('api_key_encrypted',
                              new_column_name='api_key',
                              existing_type=sa.Text(),
                              type_=sa.VARCHAR(length=255),
                              existing_nullable=True)
