"""Explicit, local administrator bootstrap. Public signup always creates customers."""
import click
from app.extensions import db
from app.models.user import User, UserStatus
from app.models.role import Role
from app.auth.utils import validate_password_strength


def register_commands(app):
    @app.cli.command('seed-content')
    def seed_content_command():
        from app.models import seed_roles
        seed_roles()
        click.echo('Roles and starter pages initialized; existing content preserved')

    @app.cli.command('seed-logistics')
    def seed_logistics_command():
        """Create the starter branch network, tariff, carriers and templates.

        Idempotent, and everything that touches a third party is seeded
        inactive and without credentials.
        """
        from app.data.seed_logistics import seed_all
        click.echo('Seeding logistics data...')
        seed_all()
        click.echo('Done. Carrier connectors and payment gateways are inactive '
                   'until credentials are entered and each one is switched on.')

    @app.cli.command('schema-check')
    def schema_check():
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        errors = []
        for table in db.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                errors.append(f'Missing table: {table.name}')
                continue
            existing = {column['name'] for column in inspector.get_columns(table.name)}
            errors.extend(f'Missing column: {table.name}.{column.name}' for column in table.columns if column.name not in existing)
        if errors:
            raise click.ClickException('\n'.join(errors))
        click.echo('All model tables and columns present; this does not audit types, indexes or data')

    @app.cli.command('create-admin')
    @click.option('--email', prompt=True)
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(email, password):
        email = email.strip().lower()
        if '@' not in email or len(email) > 120:
            raise click.ClickException('Enter a valid email address')
        errors = validate_password_strength(password)
        if errors:
            raise click.ClickException('; '.join(errors))
        if User.query.filter_by(email=email).first():
            raise click.ClickException('Account already exists; choose a new administrator email')
        import uuid
        user = User(username='admin-' + uuid.uuid4().hex, email=email,
                    first_name='Administrator', last_name='Moonlight', status=UserStatus.ACTIVE)
        user.set_password(password)
        user.roles.append(Role.query.filter_by(name='super_admin').one())
        db.session.add(user)
        db.session.commit()
        click.echo('Administrator created')
