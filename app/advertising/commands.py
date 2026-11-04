"""CLI entry point for the advertising seed.

Registered from ``create_app`` alongside the other command groups rather
than from ``app/cli.py``, so the advertising module owns its own command and
``cli.py`` does not grow an import of every feature in the application.
"""

import click

from app.advertising.seed import NEEDS_NATIVE_REVIEW, seed_advertising


def register_advertising_commands(app):
    """Attach ``flask seed-ads`` to the application."""

    @app.cli.command("seed-ads")
    def seed_ads_command():
        """Create the standard ad placements and draft festival campaigns.

        Idempotent, and nothing it creates is live: the campaigns are drafts
        with no dates and every creative and notice is inactive. Running it
        on production cannot put an advert in front of a customer.
        """
        click.echo("Seeding advertising placements, campaigns and notices...")
        created = seed_advertising()
        click.echo(
            "Created {placements} placement(s), {campaigns} campaign(s), "
            "{creatives} creative(s), {notices} notice(s).".format(**created)
        )
        click.echo("Everything is draft or inactive. Nothing is being served.")
        if created["campaigns"] or created["creatives"]:
            click.echo("\nNepali copy a native speaker still needs to check:")
            for item in NEEDS_NATIVE_REVIEW:
                click.echo(f"  - {item}")
