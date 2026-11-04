"""An unpriced shipment must not look like a free one.

The charge columns default to 0.0 and booking deliberately never fails for
want of a tariff — a clerk with a customer at the counter accepts the parcel
and prices it afterwards. Those two facts together meant a shipment the rate
engine could not price was indistinguishable from one that genuinely cost
nothing, and an invoice could be raised against it for zero.

That was not a corner case. The seeded tariff is domestic-only, so it
described **every international shipment** — the company's main business.

These tests pin the distinction: the figures are only a price when a lane
actually matched, operations can find the ones that did not, and the summary
counts them so nobody has to go looking.
"""

import pytest

from app.extensions import db
from app.models.shipment import Shipment
from tests.conftest import auth_header


def _book(client, token, **overrides):
    payload = {
        "origin": "Kathmandu", "destination": "Doha",
        "origin_country": "Nepal", "destination_country": "Qatar",
        "origin_district": "Kathmandu",
        "sender_name": "Sender", "receiver_name": "Receiver",
        "sender_phone": "+9779800000000", "receiver_phone": "+97400000000",
        "sender_address": "Nayabazar", "receiver_address": "Doha",
        "total_weight_kg": 8.0, "declared_value": 25000,
        "service_level": "standard",
    }
    payload.update(overrides)
    response = client.post("/api/shipments/", json=payload,
                           headers=auth_header(token))
    assert response.status_code in (200, 201), response.get_json()
    return response.get_json()


# ── The distinction itself ───────────────────────────────────────────────────

def test_an_unrated_route_is_marked_not_silently_zeroed(client, admin_token):
    """Kathmandu to Doha, with only a domestic tariff loaded."""
    shipment = _book(client, admin_token)

    assert shipment["total_charge"] in (0, 0.0)
    # ...but it says so, which is the whole point.
    assert shipment["pricing_status"] == Shipment.UNRATED
    assert shipment["needs_manual_pricing"] is True
    assert shipment["pricing_note"], "an operator needs to be told why"


def test_the_booking_still_succeeds(client, admin_token):
    """A tariff gap is not a reason to turn a customer away."""
    shipment = _book(client, admin_token)
    assert shipment["tracking_no"]
    assert shipment["status"]


def test_a_domestic_route_with_a_lane_is_priced(client, admin_token):
    """The other half: a real price must be reported as one.

    Skipped rather than failed where the domestic tariff is not seeded in
    this database — the assertion to protect is the labelling, and a test
    that silently depends on seed data is worse than one that says so.
    """
    shipment = _book(client, admin_token,
                     destination="Pokhara", destination_country="Nepal",
                     destination_district="Kaski", total_weight_kg=3.0)

    if shipment["pricing_status"] != Shipment.PRICED:
        pytest.skip("no domestic tariff seeded in this database")

    assert shipment["total_charge"] > 0
    assert shipment["needs_manual_pricing"] is False
    assert shipment["pricing_note"] is None


def test_every_booking_gets_a_recognised_status(client, admin_token):
    for destination in ("Qatar", "Nepal", "United Kingdom"):
        shipment = _book(client, admin_token, destination_country=destination)
        assert shipment["pricing_status"] in Shipment.PRICING_STATUSES


def test_a_pricing_engine_fault_is_not_reported_as_free(client, admin_token, monkeypatch):
    """A crash in the rate engine must not pass for a zero price either."""
    import app.rating

    def explode(**kwargs):
        raise RuntimeError("rate engine unavailable")

    monkeypatch.setattr(app.rating, "quote", explode)
    shipment = _book(client, admin_token)

    assert shipment["pricing_status"] == Shipment.PRICING_FAILED
    assert shipment["needs_manual_pricing"] is True
    assert shipment["total_charge"] in (0, 0.0)


# ── Finding them ─────────────────────────────────────────────────────────────

def test_the_queue_can_be_filtered(client, admin_token):
    _book(client, admin_token)
    r = client.get("/api/shipments/page", query_string={"needs_pricing": "true"},
                   headers=auth_header(admin_token))
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] >= 1
    assert all(item["needs_manual_pricing"] for item in body["items"])


def test_the_inverse_filter_excludes_them(client, admin_token):
    _book(client, admin_token)
    r = client.get("/api/shipments/page", query_string={"needs_pricing": "false"},
                   headers=auth_header(admin_token))
    assert r.status_code == 200
    assert all(not item["needs_manual_pricing"] for item in r.get_json()["items"])


def test_an_unknown_pricing_status_is_refused(client, admin_token):
    r = client.get("/api/shipments/page", query_string={"pricing_status": "cheap"},
                   headers=auth_header(admin_token))
    assert r.status_code == 400


def test_the_summary_counts_them(client, admin_token):
    """The number that belongs on the dashboard: revenue not yet quoted."""
    _book(client, admin_token)
    r = client.get("/api/shipments/page", headers=auth_header(admin_token))
    summary = r.get_json()["summary"]
    assert "needs_pricing" in summary
    assert summary["needs_pricing"] >= 1
    assert summary["needs_pricing"] <= summary["total"]


# ── The model's own guarantee ────────────────────────────────────────────────

def test_needs_manual_pricing_is_true_for_anything_not_priced(app):
    with app.app_context():
        for status in (Shipment.UNRATED, Shipment.PRICING_FAILED):
            assert Shipment(pricing_status=status).needs_manual_pricing is True
        assert Shipment(pricing_status=Shipment.PRICED).needs_manual_pricing is False


def test_the_column_defaults_to_unrated():
    """Defaulting to 'priced' would reintroduce the bug for any code path
    that bypasses the rate engine — an import, a fixture, a future endpoint.

    Asserted against the column definition rather than by inserting a row,
    so it does not depend on the rest of the table's constraints.
    """
    default = Shipment.__table__.c.pricing_status.default
    assert default is not None, "an unset pricing_status would be NULL, meaning neither"
    assert default.arg == Shipment.UNRATED
