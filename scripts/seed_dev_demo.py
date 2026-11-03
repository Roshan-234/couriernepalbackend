"""Populate a development database with believable operating data.

Run with:  python scripts/seed_dev_demo.py

The admin console is mostly tables and charts, so an empty database tells you
nothing about whether it works. This creates accounts, a rider, and a spread
of shipments across statuses, districts, zones and payment modes, so every
screen has something to show and the filters have something to filter.

Development only. It creates accounts with known passwords and is guarded
against running anywhere that looks like production.
"""

import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

PASSWORD = "Test1234!"
ACCOUNTS = [
    ("qa.admin@moonlight.test", "QA", "Admin", "super_admin"),
    ("qa.ops@moonlight.test", "Ops", "Manager", "admin"),
    ("qa.customer@moonlight.test", "Sita", "Rai", "customer"),
    ("qa.merchant@moonlight.test", "Everest", "Crafts", "merchant"),
    ("qa.rider@moonlight.test", "Bikash", "Tamang", "rider"),
]

LANES = [
    ("Kathmandu", "Lalitpur", "Kathmandu", "Lalitpur"),
    ("Kathmandu", "Pokhara", "Kathmandu", "Kaski"),
    ("Kathmandu", "Biratnagar", "Kathmandu", "Morang"),
    ("Kathmandu", "Butwal", "Kathmandu", "Rupandehi"),
    ("Pokhara", "Kathmandu", "Kaski", "Kathmandu"),
    ("Kathmandu", "Simikot", "Kathmandu", "Humla"),
    ("Kathmandu", "Birgunj", "Kathmandu", "Parsa"),
    ("Kathmandu", "Dhangadhi", "Kathmandu", "Kailali"),
]

RECEIVERS = [
    "Ram Bahadur Shrestha", "Gita Sharma", "Hari Prasad Adhikari",
    "Sunita Gurung", "Deepak Thapa", "Anita Maharjan", "Bimal Karki",
    "Laxmi Poudel", "Nabin Rai", "Sarita Magar",
]

CONTENTS = [
    "Handicrafts", "Documents", "Clothing", "Mobile accessories",
    "Books", "Pashmina shawls", "Spare parts", "Cosmetics",
]


def _user(email, first, last, role):
    from app.models.role import Role
    from app.models.user import User, UserStatus

    user = User.query.filter_by(email=email).first()
    if user:
        return user
    base = email.split("@")[0].replace(".", "")
    name, i = base, 1
    while User.query.filter_by(username=name).first():
        name, i = f"{base}{i}", i + 1
    user = User(username=name, email=email, first_name=first, last_name=last,
                status=UserStatus.ACTIVE)
    user.set_password(PASSWORD)
    found = Role.query.filter_by(name=role).first()
    if found:
        user.roles.append(found)
    db.session.add(user)
    db.session.flush()
    return user


def main():
    env = os.getenv("FLASK_ENV", "default")
    if env == "production":
        raise SystemExit("Refusing to seed demo accounts into production.")

    app = create_app(env)
    with app.app_context():
        uri = str(db.engine.url)
        if "mysql" in uri:
            raise SystemExit(f"Refusing to seed demo accounts into {uri}")

        from app.models.branch import Branch
        from app.models.cod import CodCollection
        from app.models.pickup import Pickup
        from app.models.rider import Rider
        from app.models.shipment import Shipment
        from app.models.tracking_event import TrackingEvent
        from app.rating import quote
        from app.shipment_status import (
            AT_DESTINATION_BRANCH, DELIVERED, DELIVERY_ATTEMPTED, IN_TRANSIT,
            ON_HOLD, OUT_FOR_DELIVERY, PENDING, PICKED_UP, label,
        )

        random.seed(20261103)
        accounts = {email: _user(email, f, l, r) for email, f, l, r in ACCOUNTS}
        db.session.commit()

        customer = accounts["qa.customer@moonlight.test"]
        merchant = accounts["qa.merchant@moonlight.test"]
        rider_user = accounts["qa.rider@moonlight.test"]

        hq = Branch.query.filter_by(code="KTM-HQ").first()
        rider = Rider.query.filter_by(user_id=rider_user.id).first()
        if not rider:
            rider = Rider(user_id=rider_user.id, rider_code="R-001",
                          branch_id=hq.id if hq else None, phone="+977-9801234567",
                          vehicle_type="bike", vehicle_number="BA 12 PA 3456",
                          status=Rider.AVAILABLE, serves_districts=["Kathmandu", "Lalitpur"])
            db.session.add(rider)
            db.session.flush()

        if Shipment.query.count() >= 20:
            print("Shipments already present; leaving them alone.")
            return

        # A spread that exercises every dashboard tile and filter.
        plan = [
            (PENDING, 3), (PICKED_UP, 2), (IN_TRANSIT, 4),
            (AT_DESTINATION_BRANCH, 2), (OUT_FOR_DELIVERY, 3),
            (DELIVERED, 6), (DELIVERY_ATTEMPTED, 2), (ON_HOLD, 1),
        ]

        created = 0
        for status, count in plan:
            for index in range(count):
                origin, dest, o_dist, d_dist = random.choice(LANES)
                # Half of the delivered parcels are forced to COD. Left to
                # chance the two sets can fail to overlap at all, and then
                # every finance screen renders empty against a database that
                # looks full - which is exactly the state that hides bugs.
                is_cod = index % 2 == 0 if status == DELIVERED else random.random() < 0.45
                weight = round(random.uniform(0.5, 12.0), 1)
                age = random.randint(0, 25)

                shipment = Shipment(
                    tracking_no="MNL" + os.urandom(5).hex().upper()[:9],
                    user_id=customer.id,
                    merchant_id=merchant.id if is_cod else None,
                    status=status,
                    origin=origin, destination=dest,
                    origin_district=o_dist, destination_district=d_dist,
                    origin_country="Nepal", destination_country="Nepal",
                    sender_name="Everest Crafts", sender_phone="+977-1-5922458",
                    receiver_name=random.choice(RECEIVERS),
                    receiver_phone=f"+977-98{random.randint(10000000, 99999999)}",
                    receiver_address=f"Ward {random.randint(1, 32)}, {dest}",
                    contents_description=random.choice(CONTENTS),
                    total_weight_kg=weight, total_packages=random.randint(1, 3),
                    service_level=random.choice(["standard", "standard", "express"]),
                    is_cod=is_cod,
                    cod_amount=round(random.uniform(800, 9000), -1) if is_cod else 0.0,
                    cod_status="pending" if is_cod else "not_applicable",
                    payment_mode="cod" if is_cod else "prepaid",
                    is_fragile=random.random() < 0.2,
                    created_at=datetime.utcnow() - timedelta(days=age, hours=random.randint(0, 23)),
                )
                if status in (OUT_FOR_DELIVERY, DELIVERY_ATTEMPTED, DELIVERED):
                    shipment.rider_id = rider.id
                if status == DELIVERED:
                    shipment.delivered_at = shipment.created_at + timedelta(days=random.randint(1, 4))
                    shipment.payment_status = "paid"
                if status == DELIVERY_ATTEMPTED:
                    shipment.delivery_attempt_count = random.randint(1, 2)

                # Price it the same way a real booking would be.
                result = quote(
                    origin_district=o_dist, destination_district=d_dist,
                    weight_kg=weight, service_level=shipment.service_level,
                    is_cod=is_cod, cod_amount=shipment.cod_amount or 0.0,
                )
                if result.get("status") == "ok":
                    shipment.freight_charge = result["freight"]
                    shipment.surcharge_total = result["surcharge_total"]
                    shipment.tax_amount = result["tax"]
                    shipment.total_charge = result["total"]
                    shipment.currency = result["currency"]
                    shipment.chargeable_weight_kg = result["weight"]["chargeable_weight_kg"]

                db.session.add(shipment)
                db.session.flush()

                db.session.add(TrackingEvent(
                    shipment_id=shipment.id, status=PENDING, location=origin,
                    description="Shipment created and awaiting pickup.",
                    timestamp=shipment.created_at,
                ))
                if status != PENDING:
                    db.session.add(TrackingEvent(
                        shipment_id=shipment.id, status=status, location=dest,
                        description=label(status),
                        timestamp=shipment.created_at + timedelta(days=1),
                    ))

                if is_cod and status == DELIVERED:
                    db.session.add(CodCollection(
                        shipment_id=shipment.id, merchant_id=merchant.id,
                        branch_id=hq.id if hq else None, rider_id=rider.id,
                        amount=shipment.cod_amount, currency="NPR",
                        collection_mode="cash", status=CodCollection.COLLECTED,
                        collected_at=shipment.delivered_at,
                    ))
                created += 1

        for i, slot in enumerate(("morning", "midday", "afternoon")):
            db.session.add(Pickup(
                pickup_no=Pickup.generate_number(),
                requested_by=merchant.id, merchant_id=merchant.id,
                branch_id=hq.id if hq else None,
                contact_name="Everest Crafts", contact_phone="+977-1-5922458",
                address="Thamel, Kathmandu", district="Kathmandu", city="Kathmandu",
                scheduled_date=date.today() + timedelta(days=i),
                time_slot=slot, package_count=random.randint(2, 15),
                estimated_weight_kg=round(random.uniform(3, 40), 1),
                status=Pickup.REQUESTED,
            ))

        db.session.commit()
        print(f"Created {created} shipments, 3 pickups, 1 rider.")
        print(f"Accounts (password {PASSWORD}):")
        for email, _f, _l, role in ACCOUNTS:
            print(f"  {email:32} {role}")


if __name__ == "__main__":
    main()
