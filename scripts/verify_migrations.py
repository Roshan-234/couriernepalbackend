"""Exercise both migrations with legacy data, using only an empty disposable DB.

Uses temporary SQLite by default. CI supplies TEST_MIGRATION_DATABASE_URL for
MySQL. Never point this script at a real application's database.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temporary = tempfile.TemporaryDirectory(prefix='courier-migration-')
os.environ['DATABASE_URL'] = os.environ.get(
    'TEST_MIGRATION_DATABASE_URL',
    'sqlite:///' + str(Path(temporary.name) / 'migration.db').replace('\\', '/'),
)
os.environ['AUTO_CREATE_SCHEMA'] = 'false'
os.environ['RATELIMIT_STORAGE_URI'] = 'memory://'

from app import create_app
from app.extensions import db
from flask_migrate import upgrade
from sqlalchemy import inspect, text

app = create_app('development')
with app.app_context():
    assert not inspect(db.engine).get_table_names(), 'Refusing to modify a nonempty database'
    upgrade(directory=str(Path(__file__).resolve().parents[1] / 'migrations'), revision='977bad5ca428')
    statements = [
        "INSERT INTO users (id, username, email, password_hash, first_name, last_name, status) VALUES (1, 'migration-test', 'migration@example.test', 'not-a-real-hash', 'Migration', 'Test', 'ACTIVE')",
        "INSERT INTO shipments (id, tracking_no, user_id) VALUES (1, 'LEGACY-TEST', 1)",
        "INSERT INTO parcels (id, shipment_id, weight_kg) VALUES (1, 1, 2.5), (2, 1, 1.5)",
        "INSERT INTO tracking_events (id, parcel_id, status, timestamp) VALUES (1, 1, 'in_transit', '2025-01-01 12:00:00'), (2, 2, 'delivered', '2025-01-02 12:00:00')",
        "INSERT INTO services (id, code, name) VALUES (1, 'legacy', 'Legacy service')",
        "INSERT INTO faqs (id, question, answer) VALUES (1, 'Legacy question', 'Legacy answer')",
        "INSERT INTO warehouses (id, code, name) VALUES (1, 'LEGACY', 'Legacy warehouse')",
    ]
    for statement in statements:
        db.session.execute(text(statement))
    db.session.commit()
    upgrade(directory=str(Path(__file__).resolve().parents[1] / 'migrations'))
    from app.models import seed_roles
    from app.models.shipment import Shipment
    from app.models.tracking_event import TrackingEvent
    from app.models.site_page import SitePage
    seed_roles()
    shipment = db.session.get(Shipment, 1)
    assert shipment.status == 'delivered'
    assert shipment.total_weight_kg == 4 and shipment.total_packages == 2
    assert TrackingEvent.query.count() == 2
    assert {event.shipment_id for event in TrackingEvent.query.all()} == {1}
    # Derived, not hardcoded. This was `== 14` and broke the moment the
    # privacy and cookie pages were added — a number nobody updates is a
    # tripwire for the next person rather than a check on this one. What
    # matters here is that seeding after a migration produces every page it
    # defines, not that there happen to be fourteen of them.
    from app.content.privacy_pages import PAGES as POLICY_PAGES
    from app.content.seed import DEFAULTS, SERVICES
    expected_paths = ({path for path, *_ in DEFAULTS}
                      | {'/services/' + slug for slug in SERVICES}
                      | {page['path'] for page in POLICY_PAGES})
    actual_paths = {path for (path,) in db.session.query(SitePage.path).all()}
    missing = expected_paths - actual_paths
    assert not missing, f'seeding did not produce: {sorted(missing)}'
    assert SitePage.query.count() == len(expected_paths)
    for name in ('services', 'faqs', 'warehouses'):
        assert db.session.execute(text(f'SELECT is_active FROM {name} WHERE id = 1')).scalar() == 1
    result = app.test_cli_runner().invoke(args=['schema-check'])
    assert result.exit_code == 0, result.output
    print('PASS: legacy records, parcel totals, tracking history, active flags and managed content survive migration')
    db.session.remove()
    db.engine.dispose()
temporary.cleanup()
