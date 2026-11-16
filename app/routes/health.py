"""Liveness, and the numbers worth watching on shared hosting.

`/api/health` stays exactly what it was: one query, no auth, cheap enough
for a monitor to call every minute.

`/api/health/metrics` is the one to open when cPanel says the account is
over its limit. It answers the question that matters there — *what is
growing* — because on a shared plan the thing that eventually kills a
site is not a slow page, it is a table nobody is pruning and a connection
pool sized for a server that is not this one.
"""

import os

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from app.auth.utils import require_roles
from app.extensions import db

health_ns = Blueprint('health', __name__)


@health_ns.get('/health')
def health_check():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ok'), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Database readiness check failed')
        return jsonify(status='unavailable'), 503


#: Tables that grow with traffic rather than with the business. Everything
#: else grows with shipments, which is growth the company wants.
_GROWTH_TABLES = (
    ('analytics_events', 'one row per page view'),
    ('audit_logs', 'one row per deliberate action'),
    ('notification_logs', 'one row per message, sent or suppressed'),
    ('user_sessions', 'one row per sign-in'),
    ('consent_records', 'one row per cookie decision'),
    ('google_reviews', 'replaced on each refresh, never accumulates'),
)


def _row_counts():
    counts = {}
    for table, note in _GROWTH_TABLES:
        try:
            total = db.session.execute(
                text(f'SELECT COUNT(*) FROM {table}')).scalar()  # noqa: S608
        except Exception:
            db.session.rollback()
            continue
        counts[table] = {'rows': int(total or 0), 'grows_with': note}
    return counts


def _database_size():
    """Bytes on disk, however this database can be asked.

    SQLite answers from the file; MySQL from information_schema. Neither is
    exact and both are close enough to notice a table running away.
    """
    try:
        url = db.engine.url
        if url.drivername.startswith('sqlite'):
            # Ask the engine, not the configured URI. Flask-SQLAlchemy
            # resolves a relative sqlite path against the instance folder,
            # so `sqlite:///app.db` is really `instance/app.db` -- reading
            # the raw URI reported "in-memory" for a database that was very
            # much on disk.
            path = url.database
            if not path or path == ':memory:':
                return {'bytes': None, 'source': 'in-memory'}
            if not os.path.isabs(path):
                path = os.path.join(current_app.instance_path, path)
            if os.path.exists(path):
                return {'bytes': os.path.getsize(path), 'source': 'file',
                        'path': path}
            return {'bytes': None, 'source': 'file-not-found', 'path': path}
        row = db.session.execute(text(
            'SELECT SUM(data_length + index_length) FROM information_schema.TABLES '
            'WHERE table_schema = DATABASE()')).scalar()
        return {'bytes': int(row) if row else None, 'source': 'information_schema'}
    except Exception:
        db.session.rollback()
        return {'bytes': None, 'source': 'unavailable'}


def _pool():
    """How many database connections this process is holding.

    On shared hosting these count against the account, so a pool sized for
    a dedicated server is a way to be throttled by the host rather than by
    the application.
    """
    try:
        pool = db.engine.pool
        return {
            'size': pool.size(),
            'checked_out': pool.checkedout(),
            'overflow': pool.overflow(),
            'configured': current_app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}),
        }
    except Exception:
        return {'size': None, 'checked_out': None}


def _retention():
    """Whether anything is actually pruning the growth tables.

    A retention schedule with `auto_purge` off on every rule is a schedule
    nobody is keeping, and that is the default this system ships with --
    deliberately, because automatic deletion is not something to switch on
    for somebody. This is where to notice it has not been switched on.
    """
    try:
        from app.models.privacy import RetentionRule
        rules = RetentionRule.query.filter_by(is_active=True).all()
        return {
            'rules': len(rules),
            'automatic': sum(1 for rule in rules if rule.auto_purge),
            'never_run': sum(1 for rule in rules if rule.last_purged_at is None),
            'unconfirmed': sum(1 for rule in rules
                               if '[TO CONFIRM' in (rule.basis or '')),
        }
    except Exception:
        db.session.rollback()
        return {'rules': 0, 'automatic': 0}


def _process():
    """This worker's own footprint, when the host lets us see it."""
    try:
        import resource  # POSIX only; absent on Windows.
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is kilobytes on Linux, bytes on macOS.
        return {'max_rss_kb': usage.ru_maxrss,
                'user_cpu_seconds': round(usage.ru_utime, 2),
                'system_cpu_seconds': round(usage.ru_stime, 2)}
    except Exception:
        return {'max_rss_kb': None,
                'note': 'not available on this platform'}


@health_ns.get('/health/metrics')
@require_roles('admin', 'super_admin')
def metrics():
    """What is growing, what is holding connections, and what is pruning it.

    Administrators only: row counts describe how much the business is
    being used, and the pool and process figures describe the host.
    """
    counts = _row_counts()
    analytics = counts.get('analytics_events', {}).get('rows', 0)
    audit = counts.get('audit_logs', {}).get('rows', 0)
    retention = _retention()

    # A plain reading of the numbers, so whoever opens this at the point of
    # being throttled does not have to interpret them first.
    warnings = []
    if retention.get('automatic', 0) == 0 and (analytics + audit) > 50_000:
        warnings.append(
            'Nothing is pruning automatically and the traffic tables hold '
            f'{analytics + audit:,} rows. Switch auto_purge on for the '
            'analytics and audit rules, or run flask retention-purge --apply '
            'on a schedule.')
    if analytics > 500_000:
        warnings.append(
            'analytics_events is large enough that the nightly rollup should '
            'be running: flask analytics-rollup.')

    return jsonify({
        'status': 'ok',
        'tables': counts,
        'database': _database_size(),
        'pool': _pool(),
        'retention': retention,
        'process': _process(),
        'warnings': warnings,
    }), 200
