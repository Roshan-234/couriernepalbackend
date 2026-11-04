"""CLI entry points for the privacy module.

Kept separate from ``app/cli.py`` so this module can be wired in with one
line from the application factory and removed with one line, rather than
growing the shared command file every time a namespace needs a seed.
"""

import os
from datetime import datetime, timedelta

import click

from app.models.privacy import DEFAULT_RETENTION


def register_privacy_commands(app):
    """Register the privacy CLI jobs on the application.

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

    @app.cli.command("privacy-exports-prune")
    @click.option("--days", type=int, default=None,
                  help="Override the retention period for this run.")
    @click.option("--apply", "apply_", is_flag=True,
                  help="Actually delete. Without this, nothing is removed.")
    def privacy_exports_prune(days, apply_):
        """Delete data-subject export bundles that have outlived their purpose.

        An export is written so that one person can be handed their records
        through whatever channel the identity check established. Once that
        has happened the file is a complete, unencrypted copy of somebody's
        account sitting in a folder, and the longer it sits the less it is
        justified by the request that produced it.

        Dry run unless ``--apply`` is typed, matching ``retention-purge``.
        The first run of a deletion job is the one that cannot be undone, so
        it has to be asked for in words.

        A pruned file's ``export_path`` is cleared on the request record, so
        the queue never points an administrator at a bundle that is gone.
        """
        from app.extensions import db
        from app.models.privacy import DataRequest
        from app.privacy_requests.routes import EXPORT_RETENTION_DAYS, _export_dir

        retain = EXPORT_RETENTION_DAYS if days is None else days
        if retain < 0:
            raise click.ClickException("--days cannot be negative")
        cutoff = datetime.utcnow() - timedelta(days=retain)
        directory = _export_dir()

        if not os.path.isdir(directory):
            click.echo(f"No export directory at {directory}; nothing to do.")
            return

        stale, kept, freed = [], 0, 0
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if not os.path.isfile(path) or not name.endswith(".json"):
                continue
            # Modification time, not the name: a file restored from backup
            # or copied in should be judged by when it actually appeared.
            modified = datetime.utcfromtimestamp(os.path.getmtime(path))
            if modified < cutoff:
                stale.append((path, os.path.getsize(path)))
                freed += os.path.getsize(path)
            else:
                kept += 1

        if not stale:
            click.echo(f"{kept} bundle(s) in {directory}, none older than "
                       f"{retain} days. Nothing to remove.")
            return

        verb = "Deleting" if apply_ else "Would delete"
        click.echo(f"{verb} {len(stale)} bundle(s) older than {retain} days "
                   f"({freed / 1024:.1f} KiB), keeping {kept}:")
        for path, size in stale:
            click.echo(f"  {os.path.basename(path)} ({size} bytes)")

        if not apply_:
            click.echo("Dry run. Re-run with --apply to delete.")
            return

        removed, cleared = 0, 0
        for path, _ in stale:
            try:
                os.remove(path)
                removed += 1
            except OSError as error:
                click.echo(f"  could not remove {path}: {error}")
                continue
            absolute = os.path.abspath(path)
            for record in DataRequest.query.filter_by(export_path=absolute).all():
                record.export_path = None
                cleared += 1
        db.session.commit()
        click.echo(f"Removed {removed} bundle(s); cleared {cleared} stale "
                   f"export path(s) on request records.")
