"""CLI entry points for the privacy module.

Kept separate from ``app/cli.py`` so this module can be wired in with one
line from the application factory and removed with one line, rather than
growing the shared command file every time a namespace needs a seed.
"""

import click

from app.models.privacy import DEFAULT_RETENTION


def register_privacy_commands(app):
    """Register ``flask seed-privacy`` on the application.

    Call from ``create_app`` alongside the other command registrations.
    """

    @app.cli.command("seed-privacy")
    def seed_privacy_command():
        """Install the default retention schedule.

        Idempotent. An existing rule keeps the ``retain_days`` and
        ``auto_purge`` an administrator set — only the label, target and
        basis are refreshed — because a re-seed that reset a period somebody
        lengthened on legal advice would be a silent policy change.

        Nothing is deleted by this command, and ``auto_purge`` stays off on
        every new rule. Seeding a schedule and starting to delete by it are
        two separate decisions.
        """
        from app.privacy_requests.routes import seed_retention_rules

        result = seed_retention_rules()
        click.echo(
            f"Retention schedule: {result['created']} created, "
            f"{result['updated']} refreshed, {result['total']} rules in the default set."
        )
        unconfirmed = sum(1 for entry in DEFAULT_RETENTION if "[TO CONFIRM" in entry[4])
        if unconfirmed:
            click.echo(
                f"{unconfirmed} of these periods are marked [TO CONFIRM with counsel]. "
                "They are starting points, not advice - review them before relying on them."
            )
        click.echo("Automatic purging is off on every rule until it is switched on by hand.")
