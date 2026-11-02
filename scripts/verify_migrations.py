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
    assert SitePage.query.count() == 14
    for name in ('services', 'faqs', 'warehouses'):
        assert db.session.execute(text(f'SELECT is_active FROM {name} WHERE id = 1')).scalar() == 1
    result = app.test_cli_runner().invoke(args=['schema-check'])
    assert result.exit_code == 0, result.output
    print('PASS: legacy records, parcel totals, tracking history, active flags and managed content survive migration')
    db.session.remove()
    db.engine.dispose()
temporary.cleanup()
