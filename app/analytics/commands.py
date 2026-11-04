"""Scheduled jobs: roll events up, and delete what is past its retention.

Two commands, registered by ``register_analytics_commands(app)`` from
``app/cli.py``.

``analytics-rollup`` exists so the dashboard does not scan the event table
and, more importantly, so the trend survives the purge: once a day's events
are aggregated, deleting them costs nothing a report needed. It is safe to
run twice, safe to run late, and safe to run over a backlog after the box
was down for a week.

``retention-purge`` is the job that makes the retention rules real rather
than aspirational. It defaults to telling you what it would delete and
requires ``--apply`` to actually do it, because a deletion job that
over-reaches on its first run cannot be undone and the rows it took were
exactly the rows nobody had a copy of.
"""

from datetime import date, datetime, time, timedelta

import click
from sqlalchemy import distinct, func

from app.extensions import db
from app.models.analytics import AnalyticsDaily, AnalyticsEvent

#: Longest backlog ``--since`` will walk in one go. A typo in a date should
#: cost a sentence of output, not an hour of aggregation.
MAX_BACKLOG_DAYS = 400


def _parse_day(value, label):
    try:
        return date.fromisoformat(value.strip()[:10])
    except (AttributeError, ValueError):
        raise click.ClickException(f"{label} must be an ISO date (YYYY-MM-DD)")


# ── rollup ───────────────────────────────────────────────────────────────────

def rollup_day(day, commit=True):
    """Aggregate one day's events into ``AnalyticsDaily``. Idempotent.

    Idempotent by looking the slice up rather than by inserting and catching
    the unique-constraint violation: a failed insert poisons the surrounding
    transaction on PostgreSQL and costs a round trip everywhere, and more to
    the point "update if present" is what the job actually means.

    ``path`` is coerced to ``""`` on both sides of the comparison. The event
    column is nullable and the rollup's unique constraint covers
    ``(day, name, path)`` — and in SQL two NULLs are not equal, so a NULL
    path would quietly produce a duplicate row on every run.

    Slices that have no events are left exactly as they are. Re-running the
    rollup after retention has removed a day's events must not zero the
    history it was computed from; that history is the only remaining record.
    """
    start, end = datetime.combine(day, time.min), datetime.combine(day, time.max)
    path_expr = func.coalesce(AnalyticsEvent.path, "")

    measured = (
        db.session.query(
            AnalyticsEvent.name,
            path_expr.label("path"),
            func.count(AnalyticsEvent.id).label("events"),
            func.count(distinct(AnalyticsEvent.visitor_key)).label("visitors"),
        )
        .filter(AnalyticsEvent.occurred_at >= start, AnalyticsEvent.occurred_at <= end)
        .group_by(AnalyticsEvent.name, path_expr)
        .all()
    )

    existing = {(row.name, row.path or ""): row
                for row in AnalyticsDaily.query.filter(AnalyticsDaily.day == day).all()}

    created = updated = events = 0
    for name, path, slice_events, slice_visitors in measured:
        path = path or ""
        row = existing.get((name, path))
        if row is None:
            row = AnalyticsDaily(day=day, name=name, path=path)
            db.session.add(row)
            created += 1
        else:
            updated += 1
        row.events = int(slice_events)
        row.visitors = int(slice_visitors)
        events += int(slice_events)

    if commit:
        db.session.commit()
    return {"day": day.isoformat(), "slices": len(measured),
            "created": created, "updated": updated, "events": events}


def rollup_range(first_day, last_day, commit=True):
    """Roll up every day in a closed range, oldest first."""
    if first_day > last_day:
        raise click.ClickException("--since must not be after the day being rolled up")
    span = (last_day - first_day).days + 1
    if span > MAX_BACKLOG_DAYS:
        raise click.ClickException(
            f"That is {span} days. Refusing more than {MAX_BACKLOG_DAYS} in one run; "
            "narrow the range or run it in batches.")
    results = []
    cursor = first_day
    while cursor <= last_day:
        results.append(rollup_day(cursor, commit=commit))
        cursor += timedelta(days=1)
    return results


# ── retention ────────────────────────────────────────────────────────────────

def _analytics_event():
    return AnalyticsEvent, AnalyticsEvent.occurred_at, None


def _audit_log():
    from app.models.audit_log import AuditLog
    return AuditLog, AuditLog.timestamp, None


def _consent_record():
    from app.models.consent import ConsentRecord
    return ConsentRecord, ConsentRecord.created_at, None


def _user_session():
    from app.models.user_session import UserSession
    # The rule is "expired and revoked sessions". A long-lived session that
    # is still valid is not stale data, it is somebody signed in.
    return UserSession, UserSession.created_at, db.or_(
        UserSession.is_revoked.is_(True),
        db.and_(UserSession.expires_at.isnot(None),
                UserSession.expires_at <= datetime.utcnow()),
    )


def _notification_log():
    from app.models.notification import NotificationLog
    return NotificationLog, NotificationLog.sent_at, None


def _data_request():
    from app.models.privacy import DataRequest
    return DataRequest, DataRequest.created_at, None


#: Which ``RetentionRule.target`` values this job knows how to delete, and
#: how. Deliberately an allow-list rather than a lookup through SQLAlchemy's
#: class registry: a rule whose target this job does not recognise is
#: reported and skipped, which is the only safe thing to do with an
#: instruction to delete rows from a table nobody has thought about.
#:
#: Two absences are on purpose. ``AdEvent`` has no model — ad impressions
#: and clicks are ``AnalyticsEvent`` rows carrying ``ad_creative_id`` — and
#: ``MarketingContact`` is not a plain age-based delete: that rule covers
#: *unsubscribed* contacts only, and a suppression record removed too early
#: means the person gets re-added by the next import. Both belong to the
#: modules that own them.
PURGE_TARGETS = {
    "AnalyticsEvent": _analytics_event,
    "AuditLog": _audit_log,
    "ConsentRecord": _consent_record,
    "UserSession": _user_session,
    "NotificationLog": _notification_log,
    "DataRequest": _data_request,
}


def purge_expired(apply=False, now=None):
    """Walk the retention rules and delete (or count) what has aged out.

    Only rules that are active and have ``auto_purge`` switched on are
    touched: automatic deletion is a decision an administrator makes per
    rule, not a default this job assumes. ``last_purged_at`` and
    ``last_purged_count`` are written only on a real run, so a dry run
    leaves no trace and can be repeated.

    Counting and deleting both happen in SQL. Nothing is loaded into Python
    to be deleted one row at a time.
    """
    from app.models.privacy import RetentionRule

    now = now or datetime.utcnow()
    report = []
    rules = (RetentionRule.query
             .filter(RetentionRule.is_active.is_(True), RetentionRule.auto_purge.is_(True))
             .order_by(RetentionRule.code.asc()).all())

    for rule in rules:
        entry = {"code": rule.code, "target": rule.target,
                 "retain_days": rule.retain_days, "deleted": 0, "applied": False}
        resolver = PURGE_TARGETS.get(rule.target)
        if resolver is None:
            entry["skipped"] = (
                f"no purge handler for target '{rule.target}'; the module that owns "
                "it has to delete these rows itself")
            report.append(entry)
            continue
        if not isinstance(rule.retain_days, int) or rule.retain_days < 1:
            entry["skipped"] = "retain_days must be a positive whole number of days"
            report.append(entry)
            continue

        try:
            model, timestamp, condition = resolver()
        except Exception as exc:  # noqa: BLE001 - a missing model is a skip, not a crash
            entry["skipped"] = f"could not load the model for '{rule.target}': {exc}"
            report.append(entry)
            continue

        cutoff = now - timedelta(days=rule.retain_days)
        entry["cutoff"] = cutoff.isoformat()
        query = db.session.query(model).filter(timestamp.isnot(None), timestamp < cutoff)
        if condition is not None:
            query = query.filter(condition)

        if not apply:
            entry["deleted"] = int(query.with_entities(func.count()).scalar() or 0)
            entry["would_delete"] = entry["deleted"]
            report.append(entry)
            continue

        deleted = query.delete(synchronize_session=False)
        rule.last_purged_at = now
        rule.last_purged_count = int(deleted or 0)
        entry["deleted"] = int(deleted or 0)
        entry["applied"] = True
        report.append(entry)

    if apply:
        db.session.commit()
    else:
        # A dry run must not leave the identity map holding anything it
        # looked at, and must certainly not write.
        db.session.rollback()
    return report


# ── registration ─────────────────────────────────────────────────────────────

def register_analytics_commands(app):
    """Attach the analytics CLI jobs. Called from ``app/cli.py``."""

    @app.cli.command("analytics-rollup")
    @click.option("--day", default=None,
                  help="The day to aggregate (YYYY-MM-DD). Defaults to yesterday.")
    @click.option("--since", default=None,
                  help="Roll up every day from this date to --day inclusive, "
                       "to catch up after downtime.")
    def analytics_rollup_command(day, since):
        """Aggregate analytics events into the daily rollup.

        Safe to run repeatedly: a day that has already been rolled up is
        updated in place rather than duplicated.
        """
        # Yesterday by default, because a rollup of today is a partial one
        # that the next run would have to redo anyway.
        last_day = _parse_day(day, "--day") if day else (datetime.utcnow().date()
                                                         - timedelta(days=1))
        first_day = _parse_day(since, "--since") if since else last_day

        total = 0
        for result in rollup_range(first_day, last_day):
            total += result["events"]
            click.echo(f"  {result['day']}: {result['slices']} slices, "
                       f"{result['created']} new, {result['updated']} updated, "
                       f"{result['events']} events")
        click.echo(f"Rolled up {total} events from {first_day} to {last_day}.")

    @app.cli.command("retention-purge")
    @click.option("--dry-run/--no-dry-run", "dry_run", default=True,
                  help="Report what would go without deleting it. The default.")
    @click.option("--apply", "apply_", is_flag=True, default=False,
                  help="Actually delete. Required; without it nothing is removed.")
    def retention_purge_command(dry_run, apply_):
        """Delete rows that have outlived their retention rule.

        A dry run by default, and deliberately so. The first real run of a
        purge job against a live database is the one that finds out the
        retention periods were wrong, and by then the rows are gone. Read
        the counts, then pass --apply.

        Only rules with auto_purge switched on are considered; a rule whose
        target this job has no handler for is listed and skipped rather than
        guessed at.
        """
        # --apply is the only thing that deletes. --no-dry-run on its own is
        # accepted and still deletes nothing: turning off a safety is not the
        # same as asking for the action, and the one that destroys data has
        # to be the one that was typed on purpose.
        dry_run = not apply_

        report = purge_expired(apply=not dry_run)
        if not report:
            click.echo("No active retention rule has auto_purge switched on. "
                       "Nothing to do.")
            return

        total = 0
        for entry in report:
            if entry.get("skipped"):
                click.echo(f"  {entry['code']}: skipped - {entry['skipped']}")
                continue
            total += entry["deleted"]
            verb = "deleted" if entry["applied"] else "would delete"
            click.echo(f"  {entry['code']} ({entry['target']}, keep "
                       f"{entry['retain_days']}d, cutoff {entry['cutoff'][:10]}): "
                       f"{verb} {entry['deleted']} rows")

        if dry_run:
            click.echo(f"Dry run: {total} rows would be deleted. "
                       "Re-run with --apply to delete them.")
        else:
            click.echo(f"Deleted {total} rows.")
