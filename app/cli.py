"""Explicit, local administrator bootstrap. Public signup always creates customers."""
import click
from app.extensions import db
from app.models.user import User, UserStatus
from app.models.role import Role
from app.auth.utils import validate_password_strength


def register_commands(app):
    # Commands that live with their own module rather than here.
    from app.analytics.commands import register_analytics_commands
    from app.privacy_requests.commands import register_privacy_commands
    from app.advertising.commands import register_advertising_commands
    register_analytics_commands(app)
    register_privacy_commands(app)
    register_advertising_commands(app)

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

    @app.cli.command('seed-demo-documents')
    def seed_demo_documents_command():
        """Create example reviews, a packing list and a freight bill.

        For looking at, not for using. Reviews are flagged `is_demo` and
        can never reach the public listing or the average rating; the two
        documents are prefixed DEMO-. Refuses to run in production.
        """
        from app.data.seed_demo_documents import seed_demo_documents
        result = seed_demo_documents()
        click.echo(f"Reviews:       {result['reviews']} created (demo, never published)")
        click.echo(f"Packing lists: {result['packing_lists']} created")
        click.echo(f"Freight bills: {result['freight_bills']} created")
        click.echo("Generate the Excel files from the admin screens, or with "
                   "flask render-demo-documents.")

    @app.cli.command('render-demo-documents')
    def render_demo_documents_command():
        """Write the demo packing list and freight bill out as Excel.

        Shows what the new format produces without anyone having to click
        through the admin screens.
        """
        import os
        from app.models.invoice import Invoice
        from app.models.packing_list import PackingList
        from app.utils.house_format import (generate_invoice_excel,
                                            generate_packing_list_excel)

        directory = os.path.abspath(
            os.path.join(app.root_path, '..', 'storage', 'demo_documents'))
        os.makedirs(directory, exist_ok=True)

        pl = PackingList.query.filter_by(pl_number='DEMO-PL-0001').first()
        bill = Invoice.query.filter_by(invoice_number='DEMO-BILL-0001').first()
        if not pl or not bill:
            raise click.ClickException('Run flask seed-demo-documents first')

        for record, render in ((pl, generate_packing_list_excel),
                               (bill, generate_invoice_excel)):
            path, name = render(record, directory)
            record.file_path, record.file_name = path, name
            click.echo(f'  {name}')
        db.session.commit()
        click.echo(f'Written to {directory}')

    @app.cli.command('seed-faqs')
    def seed_faqs_command():
        """Publish the FAQ set.

        The table was empty on every real deployment: the old defaults were
        only inserted when `current_app.testing`, the same trap the service
        catalogue fell into. Idempotent, and it never rewords an answer an
        administrator has already edited.
        """
        from app.content.faqs import seed_faqs
        result = seed_faqs()
        click.echo(f"FAQs: {result['created']} created, "
                   f"{result['total']} in the published set.")

    @app.cli.command('seed-privacy-pages')
    @click.option('--force', is_flag=True,
                  help='Replace the pages even where an administrator has edited them')
    def seed_privacy_pages_command(force):
        """Publish the privacy, cookie and data-request pages.

        The pre-existing /privacy page described Google Analytics and a
        browser-local consent flag, neither of which the site still uses, so
        that one is replaced when it is untouched. Administrator edits are
        preserved unless --force says otherwise.
        """
        from app.content.privacy_pages import seed_privacy_pages
        for path, outcome in seed_privacy_pages(force=force).items():
            click.echo(f'  {path}: {outcome}')

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
