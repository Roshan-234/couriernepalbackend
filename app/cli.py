"""Explicit, local administrator bootstrap. Public signup always creates customers."""
import click
from app.extensions import db
from app.models.user import User, UserStatus
from app.models.role import Role
from app.auth.utils import validate_password_strength


def register_commands(app):
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
