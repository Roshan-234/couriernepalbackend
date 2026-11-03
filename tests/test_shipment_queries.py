"""Query-level coverage for every shipment read the frontend performs.

The admin console drives `/api/shipments/page` with a filter per control, and
reads the `summary` block for its stat tiles. Those are SQL aggregates, so a
wrong `case()` or a missing `is_()` shows up as a plausible-looking number
rather than an error. Each test here builds a known dataset and asserts the
exact rows and totals the database returns.

Tests scope themselves to a dedicated owner wherever possible, because the
suite shares one session-scoped database with every other test file.
"""
import pytest

from app.extensions import db
from tests.conftest import auth_header, register_and_login

OWNER_EMAIL = "query.owner@example.test"
OTHER_EMAIL = "query.other@example.test"


def _csrf(client):
    cookie = client.get_cookie("csrf_access_token")
    return {"X-CSRF-TOKEN": cookie.value} if cookie else {}


@pytest.fixture(scope="module")
def owner_token(client):
    return register_and_login(client, OWNER_EMAIL)


@pytest.fixture(scope="module")
def other_token(client):
    return register_and_login(client, OTHER_EMAIL)


def _book(client, token, **overrides):
    """Create one shipment through the API and return its payload."""
    payload = {
        "origin": "Kathmandu",
        "destination": "Pokhara",
        "origin_district": "Kathmandu",
        "destination_district": "Kaski",
        "total_weight_kg": 2.0,
        "total_packages": 1,
        "sender_name": "Query Sender",
        "receiver_name": "Query Receiver",
    }
    payload.update(overrides)
    response = client.post("/api/shipments", json=payload, headers=auth_header(token))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


@pytest.fixture(scope="module")
def dataset(client, owner_token):
    """A known spread of shipments owned by one customer.

    Six rows: three plain, two COD, one international, across three
    destination districts and two service levels.
    """
    rows = [
        _book(client, owner_token, destination_district="Kaski", service_level="standard"),
        _book(client, owner_token, destination_district="Kaski", service_level="express"),
        _book(client, owner_token, destination_district="Morang", service_level="standard"),
        _book(client, owner_token, destination_district="Humla", service_level="standard",
              is_cod=True, cod_amount=1500.0),
        _book(client, owner_token, destination_district="Kaski",
              is_cod=True, cod_amount=2500.0),
        _book(client, owner_token, destination_country="India",
              destination_district=None, destination="Delhi"),
    ]
    return rows


def _page(client, token, **query):
    response = client.get("/api/shipments/page", query_string=query, headers=auth_header(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


# ── Booking persists and returns the logistics fields ───────────────────────

def test_booking_persists_logistics_fields(client, owner_token):
    """The whole point of the new columns: they must survive a round trip."""
    created = _book(
        client, owner_token,
        destination_district="Kaski",
        service_level="express",
        length_cm=40, width_cm=30, height_cm=20,
        contents_description="Documents",
        is_fragile=True,
        is_insured=True,
        insurance_amount=5000.0,
        is_cod=True,
        cod_amount=3200.0,
        reference_no="SHOP-1001",
    )
    assert created["origin_district"] == "Kathmandu"
    assert created["destination_district"] == "Kaski"
    assert created["service_level"] == "express"
    assert created["length_cm"] == 40
    assert created["is_fragile"] is True
    assert created["is_insured"] is True
    assert created["insurance_amount"] == 5000.0
    assert created["is_cod"] is True
    assert created["cod_amount"] == 3200.0
    assert created["cod_status"] == "pending"
    assert created["payment_mode"] == "cod"
    assert created["reference_no"] == "SHOP-1001"
    # Derived display fields the UI binds to.
    assert created["status_label"]
    assert created["status_group"] == "booking"
    assert created["is_terminal"] is False

    # And they must still be there on a re-read, not just echoed back.
    fetched = client.get(f"/api/shipments/{created['id']}", headers=auth_header(owner_token))
    assert fetched.status_code == 200
    assert fetched.get_json()["cod_amount"] == 3200.0
    assert fetched.get_json()["reference_no"] == "SHOP-1001"


def test_non_cod_booking_is_not_marked_cod(client, owner_token):
    created = _book(client, owner_token)
    assert created["is_cod"] is False
    assert created["cod_status"] == "not_applicable"
    assert created["payment_mode"] == "prepaid"


def test_cod_without_amount_is_rejected(client, owner_token):
    """A COD parcel with no amount is a collection the rider cannot make."""
    response = client.post("/api/shipments", json={
        "origin": "Kathmandu", "destination": "Pokhara", "is_cod": True,
    }, headers=auth_header(owner_token))
    assert response.status_code == 400
    assert "cod_amount" in response.get_data(as_text=True)


def test_unknown_district_is_rejected(client, owner_token):
    response = client.post("/api/shipments", json={
        "origin": "Kathmandu", "destination": "Nowhere",
        "destination_district": "Atlantis",
    }, headers=auth_header(owner_token))
    assert response.status_code == 400
    assert "77 districts" in response.get_data(as_text=True)


def test_district_name_is_canonicalised(client, owner_token):
    """A slug or lowercase name must store as the canonical district name."""
    created = _book(client, owner_token, destination_district="kaski")
    assert created["destination_district"] == "Kaski"


def test_client_cannot_set_its_own_charges(client, owner_token):
    """Freight is priced by the server; a client-supplied total is ignored."""
    created = _book(client, owner_token, total_charge=1, freight_charge=1, tax_amount=1)
    assert created["freight_charge"] != 1
    assert created["tax_amount"] != 1
    assert created["total_charge"] != 1


# ── Pricing on booking ──────────────────────────────────────────────────────

def test_booking_is_priced_when_a_tariff_exists(client, owner_token, app):
    """With a rate card in place, booking fills the charge breakdown."""
    from datetime import date
    from app.models.rate_card import RateCard, RateCardEntry

    with app.app_context():
        card = RateCard.query.filter_by(code="TEST-DOM").first()
        if not card:
            card = RateCard(
                code="TEST-DOM", name="Test domestic", scope="domestic",
                service_level="standard", currency="NPR", is_default=True,
                is_active=True, effective_from=date(2020, 1, 1),
                volumetric_divisor=5000, min_chargeable_weight_kg=0.5,
                weight_step_kg=0.5, vat_percent=13.0, cod_fee_percent=0.0,
                insurance_percent=0.0, fuel_surcharge_percent=0.0,
            )
            db.session.add(card)
            db.session.flush()
            db.session.add(RateCardEntry(
                rate_card_id=card.id, origin_zone="valley", destination_zone="cities",
                min_weight_kg=0.0, max_weight_kg=None,
                base_price=100.0, price_per_kg=50.0, min_charge=100.0,
                transit_days_min=2, transit_days_max=4, is_active=True,
            ))
            db.session.commit()

    created = _book(client, owner_token, total_weight_kg=2.0,
                    destination_district="Kaski", service_level="standard")
    # 100 base + 50/kg * 2kg = 200 freight, +13% VAT = 226 total.
    assert created["freight_charge"] == 200.0
    assert created["tax_amount"] == 26.0
    assert created["total_charge"] == 226.0
    assert created["chargeable_weight_kg"] == 2.0
    assert created["currency"] == "NPR"
    # Transit days drive the promised date.
    assert created["estimated_delivery"] is not None


def test_booking_survives_a_missing_tariff(client, owner_token):
    """A route with no published lane is a tariff gap, not a failed booking."""
    created = _book(client, owner_token, destination_country="Iceland",
                    destination_district=None, destination="Reykjavik")
    assert created["id"]
    assert created["total_charge"] == 0.0


# ── Filters on /shipments/page ──────────────────────────────────────────────

def test_page_scopes_to_the_caller(client, owner_token, other_token, dataset):
    """Without ?all=1 a customer sees only their own rows."""
    mine = _page(client, owner_token)
    assert mine["total"] >= len(dataset)
    theirs = _page(client, other_token)
    my_ids = {row["id"] for row in mine["items"]}
    their_ids = {row["id"] for row in theirs["items"]}
    assert my_ids.isdisjoint(their_ids)


def test_page_all_requires_admin(client, owner_token):
    response = client.get("/api/shipments/page?all=1", headers=auth_header(owner_token))
    assert response.status_code == 403


def test_filter_by_destination_district(client, owner_token, dataset):
    page = _page(client, owner_token, destination_district="Kaski", per_page=100)
    assert page["items"], "expected Kaski shipments"
    assert all(row["destination_district"] == "Kaski" for row in page["items"])
    # Cross-check the count against the dataset we know we created.
    expected = sum(1 for row in dataset if row["destination_district"] == "Kaski")
    assert page["total"] >= expected


def test_filter_by_cod(client, owner_token, dataset):
    cod = _page(client, owner_token, is_cod="1", per_page=100)
    assert cod["items"]
    assert all(row["is_cod"] is True for row in cod["items"])

    non_cod = _page(client, owner_token, is_cod="0", per_page=100)
    assert all(row["is_cod"] is False for row in non_cod["items"])

    # The two halves must partition the whole set, with no row counted twice.
    everything = _page(client, owner_token, per_page=100)
    assert cod["total"] + non_cod["total"] == everything["total"]


def test_filter_by_service_level(client, owner_token, dataset):
    page = _page(client, owner_token, service_level="express", per_page=100)
    assert page["items"]
    assert all(row["service_level"] == "express" for row in page["items"])


def test_filter_by_status(client, owner_token, dataset):
    page = _page(client, owner_token, status="pending", per_page=100)
    assert all(row["status"] == "pending" for row in page["items"])


def test_branch_filter_matches_origin_destination_or_current(client, owner_token, app):
    """'At this branch' means anything the branch sends, receives or holds."""
    from app.models.branch import Branch
    from app.models.shipment import Shipment

    with app.app_context():
        branch = Branch.query.filter_by(code="QRY-HUB").first()
        if not branch:
            branch = Branch(code="QRY-HUB", name="Query hub", type="hub",
                            district="Kathmandu", is_active=True)
            db.session.add(branch)
            db.session.commit()
        branch_id = branch.id

    made = [_book(client, owner_token) for _ in range(3)]
    with app.app_context():
        # One row per position, so a filter that only checks origin fails.
        for shipment_id, field in zip(
            [row["id"] for row in made],
            ("origin_branch_id", "destination_branch_id", "current_branch_id"),
        ):
            setattr(db.session.get(Shipment, shipment_id), field, branch_id)
        db.session.commit()

    page = _page(client, owner_token, branch_id=branch_id, per_page=100)
    found = {row["id"] for row in page["items"]}
    for row in made:
        assert row["id"] in found, "a shipment touching the branch was missed"


def test_unknown_status_filter_is_rejected(client, owner_token):
    response = client.get("/api/shipments/page?status=teleported", headers=auth_header(owner_token))
    assert response.status_code == 400


def test_bad_integer_filter_is_rejected(client, owner_token):
    response = client.get("/api/shipments/page?rider_id=abc", headers=auth_header(owner_token))
    assert response.status_code == 400


def test_bad_date_filter_is_rejected(client, owner_token):
    response = client.get("/api/shipments/page?date_from=last-tuesday", headers=auth_header(owner_token))
    assert response.status_code == 400


def test_date_range_includes_today(client, owner_token, dataset):
    """date_to must be inclusive, or today's shipments vanish from a report."""
    from datetime import date
    today = date.today().isoformat()
    page = _page(client, owner_token, date_from=today, date_to=today, per_page=100)
    assert page["total"] >= len(dataset), "today's own shipments were excluded"


def test_search_matches_tracking_reference_and_receiver(client, owner_token):
    created = _book(client, owner_token, reference_no="FINDME-77",
                    receiver_name="Unique Receiver Name")

    by_tracking = _page(client, owner_token, search=created["tracking_no"])
    assert any(row["id"] == created["id"] for row in by_tracking["items"])

    by_reference = _page(client, owner_token, search="FINDME-77")
    assert any(row["id"] == created["id"] for row in by_reference["items"])

    by_receiver = _page(client, owner_token, search="Unique Receiver")
    assert any(row["id"] == created["id"] for row in by_receiver["items"])


def test_search_treats_wildcards_literally(client, owner_token, dataset):
    """An unescaped % would match every row and look like a working search."""
    page = _page(client, owner_token, search="%", per_page=100)
    assert page["total"] == 0


# ── Summary aggregates ──────────────────────────────────────────────────────

def test_summary_counts_match_the_filtered_rows(client, owner_token, dataset):
    """The stat tiles must describe the list the operator is looking at."""
    page = _page(client, owner_token, per_page=100)
    summary = page["summary"]
    items = page["items"]
    assert summary["total"] == page["total"] == len(items)
    assert summary["pending"] == sum(1 for row in items if row["status"] == "pending")
    assert summary["delivered"] == sum(1 for row in items if row["status"] == "delivered")
    assert summary["in_transit"] == sum(1 for row in items if row["status"] == "in_transit")
    assert summary["cod_count"] == sum(1 for row in items if row["is_cod"])
    expected_cod = round(sum(row["cod_amount"] or 0 for row in items if row["is_cod"]), 2)
    assert summary["cod_amount"] == expected_cod


def test_summary_respects_a_filter(client, owner_token, dataset):
    """A COD-filtered page must not report the unfiltered totals."""
    page = _page(client, owner_token, is_cod="1", per_page=100)
    assert page["summary"]["total"] == page["total"]
    assert page["summary"]["cod_count"] == page["total"]
    assert page["summary"]["cod_amount"] > 0


def test_summary_has_the_operational_counters(client, owner_token, dataset):
    summary = _page(client, owner_token)["summary"]
    for key in ("total", "pending", "in_transit", "delivered",
                "out_for_delivery", "needs_attention", "cod_count", "cod_amount"):
        assert key in summary, f"dashboard reads summary.{key}"


# ── Pagination ──────────────────────────────────────────────────────────────

def test_pagination_does_not_repeat_or_drop_rows(client, owner_token, dataset):
    """A tie on created_at must not let a row appear on two pages."""
    size = 2
    first = _page(client, owner_token, page=1, per_page=size)
    total, pages = first["total"], first["pages"]
    assert total >= 6

    seen = []
    for page_no in range(1, pages + 1):
        seen.extend(row["id"] for row in _page(client, owner_token, page=page_no, per_page=size)["items"])
    assert len(seen) == total, "walking every page did not return every row"
    assert len(set(seen)) == total, "a shipment appeared on more than one page"


def test_per_page_is_bounded(client, owner_token):
    response = client.get("/api/shipments/page?per_page=5000", headers=auth_header(owner_token))
    assert response.status_code == 400


# ── Status transitions and authorisation ────────────────────────────────────

def test_admin_can_skip_a_scan_forward(client, owner_token, admin_token):
    """pending -> in_transit is a skipped scan, which operations do daily."""
    created = _book(client, owner_token)
    response = client.put(f"/api/shipments/{created['id']}",
                          json={"status": "in_transit", "location": "KTM hub"},
                          headers=auth_header(admin_token))
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "in_transit"
    assert body["status_label"]
    assert "next_statuses" in body


def test_a_delivered_shipment_cannot_be_reopened(client, owner_token, admin_token):
    created = _book(client, owner_token)
    endpoint = f"/api/shipments/{created['id']}"
    assert client.put(endpoint, json={"status": "delivered"},
                      headers=auth_header(admin_token)).status_code == 200
    reopened = client.put(endpoint, json={"status": "pending"},
                          headers=auth_header(admin_token))
    assert reopened.status_code == 409
    assert "Cannot move" in reopened.get_data(as_text=True)


def test_status_cannot_move_backwards(client, owner_token, admin_token):
    created = _book(client, owner_token)
    endpoint = f"/api/shipments/{created['id']}"
    client.put(endpoint, json={"status": "out_for_delivery"}, headers=auth_header(admin_token))
    backwards = client.put(endpoint, json={"status": "picked_up"}, headers=auth_header(admin_token))
    assert backwards.status_code == 409


def test_delivering_stamps_delivered_at(client, owner_token, admin_token):
    created = _book(client, owner_token)
    client.put(f"/api/shipments/{created['id']}", json={"status": "delivered"},
               headers=auth_header(admin_token))
    fetched = client.get(f"/api/shipments/{created['id']}",
                         headers=auth_header(admin_token)).get_json()
    assert fetched["delivered_at"] is not None
    assert fetched["is_terminal"] is True


def test_status_change_appends_a_tracking_event(client, owner_token, admin_token):
    created = _book(client, owner_token)
    client.put(f"/api/shipments/{created['id']}",
               json={"status": "in_transit", "location": "KTM hub", "description": "Departed"},
               headers=auth_header(admin_token))
    public = client.get(f"/api/tracking/{created['tracking_no']}").get_json()
    assert any(event["description"] == "Departed" for event in public["events"])


def test_customer_cannot_change_status(client, owner_token):
    created = _book(client, owner_token)
    response = client.put(f"/api/shipments/{created['id']}", json={"status": "delivered"},
                          headers=auth_header(owner_token))
    assert response.status_code == 403


def test_customer_cannot_read_another_customers_shipment(client, owner_token, other_token):
    created = _book(client, owner_token)
    response = client.get(f"/api/shipments/{created['id']}", headers=auth_header(other_token))
    assert response.status_code == 403


def test_tracking_lookup_is_also_scoped(client, owner_token, other_token):
    created = _book(client, owner_token)
    response = client.get(f"/api/shipments/tracking/{created['tracking_no']}",
                          headers=auth_header(other_token))
    assert response.status_code == 403


# ── Shapes the frontend binds to ────────────────────────────────────────────

REQUIRED_KEYS = (
    "id", "tracking_no", "status", "status_label", "status_group", "is_terminal",
    "origin", "destination", "origin_district", "destination_district",
    "is_cod", "cod_amount", "cod_status", "payment_status", "payment_mode",
    "freight_charge", "surcharge_total", "tax_amount", "total_charge",
    "chargeable_weight_kg", "volumetric_weight_kg", "service_level",
    "rider_id", "delivery_attempt_count", "delivered_at", "created_at",
)


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_page_rows_carry_every_key_the_table_renders(client, owner_token, dataset, key):
    row = _page(client, owner_token)["items"][0]
    assert key in row


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_detail_carries_every_key_the_table_renders(client, owner_token, dataset, key):
    """The list and the detail view must not disagree about the shape."""
    detail = client.get(f"/api/shipments/{dataset[0]['id']}",
                        headers=auth_header(owner_token)).get_json()
    assert key in detail


def test_detail_includes_relations(client, owner_token, dataset):
    detail = client.get(f"/api/shipments/{dataset[0]['id']}",
                        headers=auth_header(owner_token)).get_json()
    for key in ("parcels", "events", "attempts", "payments"):
        assert key in detail, f"the detail screen renders {key}"


def test_my_list_matches_the_detail_shape(client, owner_token, dataset):
    rows = client.get("/api/shipments/my", headers=auth_header(owner_token)).get_json()
    assert rows
    assert "status_label" in rows[0]
    assert "is_cod" in rows[0]
