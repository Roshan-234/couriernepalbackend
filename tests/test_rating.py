"""Zone-based rating engine: arithmetic, lane matching and the HTTP layer."""
import pytest

from app import rating
from app.extensions import db
from app.models.rate_card import RateCard, RateCardEntry, Surcharge
from app.models.user import User, UserStatus
from tests.conftest import auth_header, register_and_login


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_rates(app):
    """Give each test an empty rating world and leave one behind."""
    def wipe():
        RateCardEntry.query.delete()
        RateCard.query.delete()
        Surcharge.query.delete()
        db.session.commit()
    wipe()
    yield
    wipe()


def make_card(service_level="standard", **overrides):
    values = dict(
        code=f"CARD-{service_level}-{overrides.pop('suffix', '1')}",
        name="Test tariff",
        scope=RateCard.DOMESTIC,
        service_level=service_level,
        currency="NPR",
        is_default=True,
        is_active=True,
        volumetric_divisor=5000,
        min_chargeable_weight_kg=0.5,
        weight_step_kg=0.5,
        fuel_surcharge_percent=0.0,
        vat_percent=13.0,
        cod_fee_percent=0.0,
        cod_fee_min=0.0,
        insurance_percent=0.0,
    )
    values.update(overrides)
    card = RateCard(**values)
    db.session.add(card)
    db.session.commit()
    return card


def _merchant_user():
    """A contract merchant, created once and reused across runs."""
    email = "rating_merchant@test.com"
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(username="rating_merchant", email=email, first_name="Rate",
                    last_name="Merchant", status=UserStatus.ACTIVE)
        user.set_password("Admin1234!")
        db.session.add(user)
        db.session.commit()
    return user


def add_entry(card, **overrides):
    values = dict(min_weight_kg=0.0, max_weight_kg=None, base_price=100.0,
                  price_per_kg=50.0, min_charge=0.0, is_active=True)
    values.update(overrides)
    entry = RateCardEntry(rate_card_id=card.id, **values)
    db.session.add(entry)
    db.session.commit()
    return entry


# ── weight arithmetic ────────────────────────────────────────────────────────

def test_volumetric_weight_uses_the_divisor():
    # 60 x 50 x 40 cm / 5000 = 24 kg
    assert rating.volumetric_weight(60, 50, 40, 5000) == 24.0
    assert rating.volumetric_weight(60, 50, 40, 6000) == 20.0


def test_volumetric_beats_actual_for_a_large_light_box():
    charge, detail = rating.chargeable_weight(3.0, 60, 50, 40, divisor=5000,
                                              minimum_kg=0.5, step_kg=0.5)
    assert charge == 24.0
    assert detail["basis"] == "volumetric"
    assert detail["actual_weight_kg"] == 3.0


def test_actual_beats_volumetric_for_a_small_heavy_box():
    charge, detail = rating.chargeable_weight(12.0, 20, 20, 20, divisor=5000,
                                              minimum_kg=0.5, step_kg=0.5)
    assert charge == 12.0
    assert detail["basis"] == "actual"


@pytest.mark.parametrize("length,width,height", [
    (0, 50, 40), (60, 0, 40), (60, 50, 0), (None, 50, 40), (60, 50, None),
])
def test_a_missing_dimension_never_produces_a_volumetric_weight(length, width, height):
    charge, detail = rating.chargeable_weight(3.0, length, width, height,
                                              divisor=5000, minimum_kg=0.5, step_kg=0.5)
    assert detail["volumetric_weight_kg"] == 0.0
    assert detail["basis"] == "actual"
    assert charge == 3.0


@pytest.mark.parametrize("raw,step,expected", [
    (1.2, 0.5, 1.5),
    (1.5, 0.5, 1.5),      # exactly on a boundary must not jump a band
    (1.51, 0.5, 2.0),
    (2.0, 1.0, 2.0),
    (2.01, 1.0, 3.0),
    (0.7, 0.25, 0.75),
])
def test_rounding_goes_up_to_the_weight_step(raw, step, expected):
    assert rating.round_up_to_step(raw, step) == expected


def test_minimum_chargeable_weight_applies():
    charge, detail = rating.chargeable_weight(0.1, divisor=5000,
                                              minimum_kg=0.5, step_kg=0.5)
    assert charge == 0.5
    assert detail["min_chargeable_applied"] is True


def test_minimum_is_applied_before_the_step():
    # 0.3 kg actual, 0.6 kg minimum, 0.5 kg step -> 0.6 floors then rounds to 1.0
    charge, _ = rating.chargeable_weight(0.3, divisor=5000, minimum_kg=0.6, step_kg=0.5)
    assert charge == 1.0


# ── lane matching ────────────────────────────────────────────────────────────

def test_a_district_lane_beats_a_zone_lane(app, clean_rates):
    card = make_card(service_level="lane-test")
    add_entry(card, origin_zone="valley", destination_zone="cities", base_price=200.0)
    add_entry(card, origin_district="Kathmandu", destination_district="Kaski",
              base_price=900.0)

    result = rating.quote(origin_district="Kathmandu", destination_district="Kaski",
                          weight_kg=1.0, service_level="lane-test")
    assert result["status"] == rating.OK
    assert result["lane"]["specificity"] == 8
    assert result["freight"] == 900.0 + 50.0


def test_a_zone_lane_is_used_when_no_district_lane_exists(app, clean_rates):
    card = make_card(service_level="lane-fallback")
    add_entry(card, origin_zone="valley", destination_zone="cities", base_price=200.0)

    result = rating.quote(origin_district="Kathmandu", destination_district="Kaski",
                          weight_kg=1.0, service_level="lane-fallback")
    assert result["status"] == rating.OK
    assert result["lane"]["specificity"] == 4
    assert result["freight"] == 250.0


def test_weight_bands_pick_the_covering_entry(app, clean_rates):
    card = make_card(service_level="band-test")
    add_entry(card, destination_zone="cities", min_weight_kg=0.0, max_weight_kg=1.0,
              base_price=100.0, price_per_kg=0.0)
    add_entry(card, destination_zone="cities", min_weight_kg=1.0, max_weight_kg=None,
              base_price=300.0, price_per_kg=0.0)

    light = rating.quote(destination_district="Kaski", weight_kg=0.5,
                         service_level="band-test")
    heavy = rating.quote(destination_district="Kaski", weight_kg=5.0,
                         service_level="band-test")
    assert light["freight"] == 100.0
    assert heavy["freight"] == 300.0


def test_min_charge_floors_the_freight(app, clean_rates):
    card = make_card(service_level="floor-test")
    add_entry(card, destination_zone="cities", base_price=10.0, price_per_kg=5.0,
              min_charge=250.0)

    result = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="floor-test")
    assert result["freight"] == 250.0


def test_no_rate_is_an_answer_not_an_exception(app, clean_rates):
    missing_card = rating.quote(destination_district="Kaski", weight_kg=1.0,
                                service_level="nothing-here")
    assert missing_card["status"] == rating.NO_RATE
    assert "message" in missing_card

    card = make_card(service_level="empty-card")
    add_entry(card, destination_zone="remote", base_price=100.0)
    missing_lane = rating.quote(destination_district="Kaski", weight_kg=1.0,
                                service_level="empty-card")
    assert missing_lane["status"] == rating.NO_RATE


# ── surcharges, fees and tax ─────────────────────────────────────────────────

def test_fuel_cod_insurance_and_vat_stack_in_order(app, clean_rates):
    card = make_card(service_level="charges-test", fuel_surcharge_percent=10.0,
                     cod_fee_percent=2.0, cod_fee_min=50.0, insurance_percent=1.0,
                     vat_percent=13.0)
    add_entry(card, destination_zone="cities", base_price=1000.0, price_per_kg=0.0)

    result = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="charges-test", is_cod=True, cod_amount=5000.0,
                          is_insured=True, declared_value=20000.0)

    amounts = {line["code"]: line["amount"] for line in result["surcharges"]}
    assert result["freight"] == 1000.0
    assert amounts["FUEL"] == 100.0           # 10% of freight
    assert amounts["COD_FEE"] == 100.0        # max(2% of 5000, 50)
    assert amounts["INSURANCE"] == 200.0      # 1% of 20000
    assert result["surcharge_total"] == 400.0
    assert result["tax"] == round(1400.0 * 0.13, 2)
    assert result["total"] == round(1400.0 + 1400.0 * 0.13, 2)


def test_cod_fee_respects_its_minimum(app, clean_rates):
    card = make_card(service_level="cod-min", cod_fee_percent=1.0, cod_fee_min=75.0,
                     vat_percent=0.0)
    add_entry(card, destination_zone="cities", base_price=500.0, price_per_kg=0.0)

    result = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="cod-min", is_cod=True, cod_amount=1000.0)
    amounts = {line["code"]: line["amount"] for line in result["surcharges"]}
    assert amounts["COD_FEE"] == 75.0


def test_a_remote_surcharge_only_fires_for_remote_destinations(app, clean_rates):
    card = make_card(service_level="remote-test", vat_percent=0.0)
    add_entry(card, destination_zone="remote", base_price=500.0, price_per_kg=0.0)
    add_entry(card, destination_zone="cities", base_price=500.0, price_per_kg=0.0)
    db.session.add(Surcharge(code="REMOTE", name="Remote area", applies_to="remote",
                             calculation=Surcharge.FLAT, value=300.0, is_active=True))
    db.session.commit()

    remote = rating.quote(destination_district="Mustang", weight_kg=1.0,
                          service_level="remote-test")
    city = rating.quote(destination_district="Kaski", weight_kg=1.0,
                        service_level="remote-test")
    assert [l["code"] for l in remote["surcharges"]] == ["REMOTE"]
    assert city["surcharges"] == []


def test_a_non_taxable_surcharge_is_excluded_from_vat(app, clean_rates):
    card = make_card(service_level="tax-test", vat_percent=13.0)
    add_entry(card, destination_zone="cities", base_price=1000.0, price_per_kg=0.0)
    db.session.add(Surcharge(code="HANDLING", name="Handling", applies_to="all",
                             calculation=Surcharge.FLAT, value=200.0,
                             is_taxable=False, is_active=True))
    db.session.commit()

    result = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="tax-test")
    assert result["taxable_amount"] == 1000.0
    assert result["tax"] == 130.0
    assert result["total"] == 1330.0


def test_surcharge_caps_are_honoured(app, clean_rates):
    card = make_card(service_level="cap-test", vat_percent=0.0)
    add_entry(card, destination_zone="cities", base_price=1000.0, price_per_kg=0.0)
    db.session.add(Surcharge(code="CAPPED", name="Capped percent", applies_to="all",
                             calculation=Surcharge.PERCENT, value=50.0,
                             max_amount=120.0, is_active=True))
    db.session.commit()

    result = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="cap-test")
    assert result["surcharge_total"] == 120.0


def test_transit_days_come_from_the_matched_lane(app, clean_rates):
    card = make_card(service_level="transit-test")
    add_entry(card, destination_zone="cities", base_price=100.0,
              transit_days_min=2, transit_days_max=4)

    result = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="transit-test")
    assert result["transit_days"] == {"min": 2, "max": 4}


def test_a_merchant_contract_card_beats_the_default(app, clean_rates):
    merchant = _merchant_user()
    default = make_card(service_level="contract-test", suffix="default")
    add_entry(default, destination_zone="cities", base_price=1000.0, price_per_kg=0.0)
    contract = make_card(service_level="contract-test", suffix="contract",
                         is_default=False, merchant_id=merchant.id)
    add_entry(contract, destination_zone="cities", base_price=600.0, price_per_kg=0.0)

    listed = rating.quote(destination_district="Kaski", weight_kg=1.0,
                          service_level="contract-test")
    negotiated = rating.quote(destination_district="Kaski", weight_kg=1.0,
                              service_level="contract-test", merchant_id=merchant.id)
    assert listed["freight"] == 1000.0
    assert listed["rate_card"]["is_contract"] is False
    assert negotiated["freight"] == 600.0
    assert negotiated["rate_card"]["is_contract"] is True


def test_an_international_destination_selects_the_international_card(app, clean_rates):
    domestic = make_card(service_level="intl-test", suffix="dom")
    add_entry(domestic, destination_zone="cities", base_price=500.0, price_per_kg=0.0)
    overseas = make_card(service_level="intl-test", suffix="intl",
                         scope=RateCard.INTERNATIONAL, currency="NPR")
    add_entry(overseas, destination_country="India", base_price=1800.0, price_per_kg=0.0)

    result = rating.quote(origin_district="Kathmandu", destination_country="India",
                          weight_kg=1.0, service_level="intl-test")
    assert result["scope"] == "international"
    assert result["freight"] == 1800.0


# ── HTTP layer ───────────────────────────────────────────────────────────────

def test_public_quote_needs_no_auth(app, client, clean_rates):
    card = make_card(service_level="http-public", vat_percent=13.0)
    add_entry(card, origin_zone="valley", destination_zone="cities",
              base_price=200.0, price_per_kg=80.0, transit_days_min=1,
              transit_days_max=2)

    r = client.post("/api/rates/quote", json={
        "origin_district": "Kathmandu",
        "destination_district": "kaski",          # slug form must resolve too
        "weight_kg": 2.0,
        "service_level": "http-public",
    })
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["status"] == "ok"
    assert body["chargeable_weight_kg"] == 2.0
    assert body["freight"] == 360.0
    assert body["currency"] == "NPR"
    assert body["lane"]["destination_district"] == "Kaski"


def test_quote_rejects_an_unknown_district(app, client, clean_rates):
    r = client.post("/api/rates/quote", json={
        "origin_district": "Kathmandu",
        "destination_district": "Atlantis",
        "weight_kg": 1.0,
    })
    assert r.status_code == 400
    assert "77 districts" in str(r.get_json())


def test_quote_rejects_a_non_positive_weight(app, client, clean_rates):
    r = client.post("/api/rates/quote", json={
        "destination_district": "Kaski", "weight_kg": 0,
    })
    assert r.status_code == 400


def test_quote_returns_404_when_nothing_is_priced(app, client, clean_rates):
    r = client.post("/api/rates/quote", json={
        "destination_district": "Kaski", "weight_kg": 1.0,
        "service_level": "no-such-level",
    })
    assert r.status_code == 404
    assert r.get_json()["status"] == "no_rate"


def test_quote_uses_dimensions_from_the_request(app, client, clean_rates):
    card = make_card(service_level="http-dims", vat_percent=0.0)
    add_entry(card, destination_zone="cities", base_price=0.0, price_per_kg=10.0)

    r = client.post("/api/rates/quote", json={
        "destination_district": "Kaski", "weight_kg": 3.0,
        "length_cm": 60, "width_cm": 50, "height_cm": 40,
        "service_level": "http-dims",
    })
    body = r.get_json()
    assert body["weight"]["basis"] == "volumetric"
    assert body["chargeable_weight_kg"] == 24.0
    assert body["freight"] == 240.0


def test_zones_endpoint_lists_all_77_districts(client):
    r = client.get("/api/rates/zones")
    body = r.get_json()
    assert r.status_code == 200
    assert len(body["districts"]) == 77
    assert {z["code"] for z in body["zones"]} == {"valley", "cities", "hills", "remote"}


def test_a_non_admin_cannot_create_a_rate_card(app, client, clean_rates):
    token = register_and_login(client, "rating_customer@test.com")
    r = client.post("/api/rates/cards", headers=auth_header(token),
                    json={"code": "SNEAKY", "name": "Not allowed"})
    assert r.status_code == 403
    assert RateCard.query.filter_by(code="SNEAKY").first() is None


def test_anonymous_cannot_list_rate_cards(client, clean_rates):
    assert client.get("/api/rates/cards").status_code in (401, 403)


def test_admin_card_lifecycle(app, client, admin_token, clean_rates):
    headers = auth_header(admin_token)

    created = client.post("/api/rates/cards", headers=headers, json={
        "code": "HTTP-CARD", "name": "HTTP tariff", "scope": "domestic",
        "service_level": "http-crud", "vat_percent": 13.0,
    })
    assert created.status_code == 201, created.get_json()
    card_id = created.get_json()["id"]

    duplicate = client.post("/api/rates/cards", headers=headers,
                            json={"code": "HTTP-CARD", "name": "Clash"})
    assert duplicate.status_code == 409

    listed = client.get("/api/rates/cards", headers=headers)
    assert listed.status_code == 200
    assert listed.get_json()["total"] == 1

    updated = client.put(f"/api/rates/cards/{card_id}", headers=headers,
                         json={"fuel_surcharge_percent": 7.5})
    assert updated.status_code == 200
    assert updated.get_json()["fuel_surcharge_percent"] == 7.5
    assert updated.get_json()["name"] == "HTTP tariff"

    removed = client.delete(f"/api/rates/cards/{card_id}", headers=headers)
    assert removed.status_code == 200
    assert db.session.get(RateCard, card_id).is_active is False


def test_card_create_rejects_unknown_fields(app, client, admin_token, clean_rates):
    r = client.post("/api/rates/cards", headers=auth_header(admin_token), json={
        "code": "MASS", "name": "Mass assignment", "id": 999, "created_at": "now",
    })
    assert r.status_code == 400


def test_entry_crud_and_zone_validation(app, client, admin_token, clean_rates):
    headers = auth_header(admin_token)
    card = make_card(service_level="entry-crud")

    bad_zone = client.post(f"/api/rates/cards/{card.id}/entries", headers=headers,
                           json={"destination_zone": "moon", "base_price": 100})
    assert bad_zone.status_code == 400

    bad_district = client.post(f"/api/rates/cards/{card.id}/entries", headers=headers,
                               json={"destination_district": "Atlantis", "base_price": 100})
    assert bad_district.status_code == 400

    created = client.post(f"/api/rates/cards/{card.id}/entries", headers=headers, json={
        "origin_zone": "valley", "destination_zone": "hills",
        "base_price": 150.0, "price_per_kg": 60.0, "transit_days_min": 2,
        "transit_days_max": 5,
    })
    assert created.status_code == 201, created.get_json()
    entry_id = created.get_json()["id"]

    updated = client.put(f"/api/rates/entries/{entry_id}", headers=headers, json={
        "origin_zone": "valley", "destination_zone": "hills", "base_price": 175.0,
    })
    assert updated.status_code == 200
    assert updated.get_json()["base_price"] == 175.0

    detail = client.get(f"/api/rates/cards/{card.id}", headers=headers)
    assert len(detail.get_json()["entries"]) == 1

    removed = client.delete(f"/api/rates/entries/{entry_id}", headers=headers)
    assert removed.status_code == 200
    assert RateCardEntry.query.filter_by(rate_card_id=card.id).count() == 0


def test_an_entry_must_pin_down_a_lane(app, client, admin_token, clean_rates):
    card = make_card(service_level="lane-required")
    r = client.post(f"/api/rates/cards/{card.id}/entries",
                    headers=auth_header(admin_token), json={"base_price": 100.0})
    assert r.status_code == 400


def test_bulk_entry_load(app, client, admin_token, clean_rates):
    headers = auth_header(admin_token)
    card = make_card(service_level="bulk-test")
    rows = [
        {"origin_zone": "valley", "destination_zone": zone,
         "min_weight_kg": low, "max_weight_kg": high,
         "base_price": 100.0 + 10 * low, "price_per_kg": 40.0}
        for zone in ("valley", "cities", "hills", "remote")
        for low, high in ((0.0, 1.0), (1.0, 5.0), (5.0, None))
    ]
    r = client.post(f"/api/rates/cards/{card.id}/entries/bulk", headers=headers,
                    json={"entries": rows})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["created"] == 12
    assert RateCardEntry.query.filter_by(rate_card_id=card.id).count() == 12


def test_bulk_load_can_replace_a_whole_tariff(app, client, admin_token, clean_rates):
    headers = auth_header(admin_token)
    card = make_card(service_level="bulk-replace")
    add_entry(card, destination_zone="hills", base_price=1.0)

    r = client.post(f"/api/rates/cards/{card.id}/entries/bulk", headers=headers,
                    json={"replace": True,
                          "entries": [{"destination_zone": "cities", "base_price": 250.0}]})
    assert r.status_code == 201
    remaining = RateCardEntry.query.filter_by(rate_card_id=card.id).all()
    assert [e.destination_zone for e in remaining] == ["cities"]


def test_bulk_load_is_capped_and_atomic(app, client, admin_token, clean_rates):
    headers = auth_header(admin_token)
    card = make_card(service_level="bulk-guard")

    too_many = client.post(f"/api/rates/cards/{card.id}/entries/bulk", headers=headers,
                           json={"entries": [{"destination_zone": "hills"}] * 501})
    assert too_many.status_code == 400

    # One bad row must leave nothing behind.
    mixed = client.post(f"/api/rates/cards/{card.id}/entries/bulk", headers=headers,
                        json={"entries": [{"destination_zone": "hills", "base_price": 10.0},
                                          {"destination_zone": "atlantis"}]})
    assert mixed.status_code == 400
    assert RateCardEntry.query.filter_by(rate_card_id=card.id).count() == 0


def test_surcharge_crud_validates_the_model_tuples(app, client, admin_token, clean_rates):
    headers = auth_header(admin_token)

    bad_applies = client.post("/api/rates/surcharges", headers=headers, json={
        "code": "BAD", "name": "Bad", "applies_to": "eclipse"})
    assert bad_applies.status_code == 400

    bad_calc = client.post("/api/rates/surcharges", headers=headers, json={
        "code": "BAD", "name": "Bad", "calculation": "vibes"})
    assert bad_calc.status_code == 400

    created = client.post("/api/rates/surcharges", headers=headers, json={
        "code": "FRAGILE", "name": "Fragile handling", "applies_to": "fragile",
        "calculation": "flat", "value": 150.0, "is_taxable": True})
    assert created.status_code == 201, created.get_json()
    surcharge_id = created.get_json()["id"]

    listed = client.get("/api/rates/surcharges", headers=headers)
    assert listed.get_json()["total"] == 1

    updated = client.put(f"/api/rates/surcharges/{surcharge_id}", headers=headers,
                         json={"value": 175.0})
    assert updated.status_code == 200
    assert updated.get_json()["value"] == 175.0
    assert updated.get_json()["applies_to"] == "fragile"


def test_legacy_pricing_calculate_is_untouched(client):
    """The public website still posts to /api/pricing/calculate."""
    r = client.post("/api/pricing/calculate", json={
        "service_type": "international", "shipment_type": "standard",
        "origin_country": "Nepal", "destination_country": "India", "weight": 1.0,
    })
    assert r.status_code in (200, 404)
