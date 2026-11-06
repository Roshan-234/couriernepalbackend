"""Example reviews, packing lists and freight bills, for looking at.

None of this is real. It exists so the screens can be built, demonstrated
and reviewed before the company's own content arrives, and every row it
creates is marked so that it cannot be mistaken for the real thing.

* Reviews are created with ``is_demo=True``. They never appear in the
  public listing, are never counted in the average, and the API refuses
  to approve one. Inventing reviews and marking them up as real is
  against Google's structured data policy and, more plainly, is lying to
  customers about what other customers said.
* Packing lists and freight bills are prefixed ``DEMO-`` so they are
  obvious in a list and simple to delete.

The command refuses to run against a production configuration. Demo data
in a live database is how a fake review ends up on a real website.
"""

from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models.invoice import Invoice
from app.models.packing_list import PackingList
from app.models.review import Review
from app.models.user import User

#: rating, title, body, author, country, verified, reply
REVIEWS = [
    (5, "Parcel reached Doha in four days",
     "Sent two boxes of clothes and dried food to my brother in Doha. Packed "
     "at the counter in Nayabazar on Sunday and he had them on Thursday. The "
     "tracking number worked the whole way, which it did not with the company "
     "I used before.",
     "Sujata K.", "QA", True,
     "Thank you Sujata. Glad the tracking was useful - please ask for the "
     "same service next time and we will match the rate."),

    (5, "They sorted out the customs paperwork",
     "I was sending handicrafts to a buyer in Germany and had no idea what "
     "documents were needed. The office prepared everything and told me what "
     "the buyer had to do at their end. Shipment cleared without a problem.",
     "Bikash T.", "NP", True, None),

    (4, "Good service, worth asking about sea freight",
     "Air freight was more than I expected for the weight, but they explained "
     "the volumetric calculation and suggested sea cargo for the heavy part of "
     "the order. Ended up splitting the shipment and saving a fair amount.",
     "Anita G.", "GB", True, None),

    (5, "Excess baggage before flying to Sydney",
     "Flew out for my studies and could not take everything. Left three boxes "
     "with them and all three arrived. Reasonable price compared with what the "
     "airline wanted for the extra weight.",
     "Prabin S.", "AU", True, None),

    (3, "Delivery was slower than quoted",
     "The parcel did get to Texas but it took about a week longer than the "
     "estimate I was given. The office kept me updated when I called, so I "
     "knew where it was, but I would have liked to be told without having to "
     "ring them.",
     "Rajan M.", "US", True,
     "You are right, and we should have called you. We have since set up "
     "automatic updates at each stage so this does not happen again. Sorry "
     "for the delay."),

    (5, "Regular shipper, no complaints",
     "We send orders to the UK most weeks. Pickup from our workshop, the "
     "paperwork is always right, and the monthly account makes the "
     "bookkeeping simple.",
     "Mira Handicrafts", "NP", True, None),

    (2, "Box arrived damaged",
     "One of two boxes was crushed at a corner and a ceramic piece inside was "
     "broken. The claim was dealt with and I was paid for the item, but the "
     "packing could have been better in the first place.",
     "Deepak R.", "NP", True,
     "We are sorry about this. The claim was settled the same week, and we "
     "have changed how ceramics are packed - they now go in a double-walled "
     "carton with the fragile surcharge included rather than optional."),

    (4, "Helpful with a difficult destination",
     "Needed to send documents to a small town in Japan and other couriers "
     "would not quote. They found a route and it arrived.",
     "Sabina L.", "JP", False, None),
]

PACKING_LIST_BOXES = [
    ("BOX NO:01", [
        ("PASHMINA SHAWL 70x200", 24, "45x35x30", ""),
        ("FELT CHRISTMAS DECORATION", 60, "", "assorted colours"),
        ("WOODEN BUDDHA STATUE 8\"", 6, "", ""),
    ]),
    ("BOX NO:02", [
        ("HEMP TOTE BAG", 36, "50x40x35", ""),
        ("COTTON KURTHA", 28, "", "mixed sizes"),
        ("SINGING BOWL 5\"", 9, "", "wrapped individually"),
    ]),
    ("BOX NO:03", [
        ("LOKTA PAPER NOTEBOOK", 120, "40x30x25", ""),
        ("INCENSE BOX", 45, "", ""),
    ]),
]

BILL_CHARGES = [
    ("Air freight charge 52kgs x 3.40", 176.80),
    ("AWB fee + insurance fee", 19.25),
    ("Export documentation", 12.00),
]


def _guard():
    if current_app.config.get("ENV_NAME") == "production":
        raise RuntimeError(
            "Demo content must never be seeded into production. A fake review "
            "in a live database is how one ends up on the website.")


def seed_demo_documents():
    """Create the example rows. Idempotent, and safe to run twice."""
    _guard()

    author = User.query.filter(User.id.isnot(None)).first()
    if author is None:
        raise RuntimeError("Seed an administrator first: flask create-admin")

    created = {"reviews": 0, "packing_lists": 0, "freight_bills": 0}
    now = datetime.utcnow()

    for index, (rating, title, body, name, country, verified, reply) in enumerate(REVIEWS):
        if Review.query.filter_by(title=title, is_demo=True).first():
            continue
        review = Review(
            rating=rating, title=title, body=body, author_name=name,
            author_country=country, language="en",
            # Demo rows are deliberately left pending as well as flagged:
            # two independent reasons they cannot reach the public listing.
            status=Review.PENDING, is_demo=True,
            created_at=now - timedelta(days=index * 9 + 3),
        )
        if reply:
            review.reply_body = reply
            review.reply_at = now - timedelta(days=index * 9 + 1)
        db.session.add(review)
        created["reviews"] += 1

    if not PackingList.query.filter_by(pl_number="DEMO-PL-0001").first():
        items = []
        for box, contents in PACKING_LIST_BOXES:
            for description, quantity, cms, remarks in contents:
                items.append({"description": description, "quantity": quantity,
                              "cms": cms, "remarks": remarks, "box": box})
        db.session.add(PackingList(
            pl_number="DEMO-PL-0001",
            created_by=author.id,
            sender_name="EXAMPLE CRAFT EXPORTS PVT. LTD.",
            sender_address="P.O.BOX NO:1234\nTHAMEL, KATHMANDU\nNEPAL",
            sender_phone="977-1-0000000",
            sender_email="exports@example.invalid",
            receiver_name="EXAMPLE IMPORTS BVBA",
            receiver_address="C/O WAREHOUSE 4\nINDUSTRIELAAN 22",
            receiver_country="BELGIUM",
            receiver_phone="02 000 0000",
            invoice_no="ECE/01/2026",
            mode_of_shipment="BY-AIR",
            goods_summary="NEPALESE HANDICRAFT & TEXTILE GOODS",
            quantity_unit="PCS",
            items=items,
            total_packages=len(PACKING_LIST_BOXES),
            total_items=sum(item["quantity"] for item in items),
            status="draft",
        ))
        created["packing_lists"] += 1

    if not Invoice.query.filter_by(invoice_number="DEMO-BILL-0001").first():
        subtotal = sum(amount for _, amount in BILL_CHARGES)
        fx_rate = 142.35
        db.session.add(Invoice(
            invoice_number="DEMO-BILL-0001",
            created_by=author.id,
            seller_name="MOONLIGHT FREIGHT P. LTD.",
            seller_country="KATHMANDU,NEPAL",
            seller_phone=current_app.config.get("COMPANY_PHONE", ""),
            seller_email="moonlightfreight@gmail.com",
            buyer_name="Example Craft Exports Pvt. Ltd.",
            buyer_address="THAMEL, KATHMANDU",
            buyer_country="Nepal",
            awb_no="257-00000000",
            account_name="Example Account",
            currency="USD",
            # Each line is a lump sum: the arithmetic is in the description,
            # which is how a freight charge is written.
            items=[{"description": description, "quantity": 1,
                    "unit_price": amount, "total_price": amount}
                   for description, amount in BILL_CHARGES],
            subtotal=round(subtotal, 2),
            total_amount=round(subtotal, 2),
            fx_rate=fx_rate,
            total_npr=round(subtotal * fx_rate, 2),
            status="draft",
        ))
        created["freight_bills"] += 1

    db.session.commit()
    return created
