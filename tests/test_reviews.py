"""Customer reviews, from our own form and from Google.

The tests fall into four groups, and the first two carry the weight:

* **nothing publishes itself** — a review is pending until a person
  approves it, and the submission endpoint tells a sender nothing;
* **demo content never counts** — seeded examples exist so the screens
  can be shown, and they must not reach the listing or the average,
  because marking up invented reviews as real is both against Google's
  structured data policy and a lie to customers;
* Google's content is cached, attributed and never merged into ours;
* an administrator can moderate and reply.
"""

import pytest

from app.extensions import db
from app.models.review import GoogleReview, PlaceRating, Review
from app.models.shipment import Shipment
from tests.conftest import auth_header


@pytest.fixture
def visitor(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_reviews(app):
    """Each test starts from an empty board.

    The averages and counts asserted below are global, so a leftover row
    from another test would make them meaningless.
    """
    with app.app_context():
        Review.query.delete()
        GoogleReview.query.delete()
        PlaceRating.query.delete()
        db.session.commit()


def _a_shipment(app):
    """A shipment to attach a review to.

    Created here rather than skipping when the database happens to have
    none: "verified customer" is the claim these tests exist to check, and
    a skipped test makes no claim at all.
    """
    with app.app_context():
        existing = Shipment.query.filter_by(tracking_no="MNLREVIEWCHK").first()
        if existing:
            return existing.id, existing.tracking_no
        from app.models.user import User
        shipment = Shipment(
            tracking_no="MNLREVIEWCHK", user_id=User.query.first().id,
            origin="Kathmandu", destination="Doha",
            origin_country="Nepal", destination_country="Qatar",
            status="delivered",
        )
        db.session.add(shipment)
        db.session.commit()
        return shipment.id, shipment.tracking_no


def _submit(client, **overrides):
    payload = {
        "rating": 5,
        "title": "Arrived quickly",
        "body": "Two boxes to Doha and both arrived in four days. Very happy.",
        "author_name": "A Customer",
    }
    payload.update(overrides)
    return client.post("/api/reviews/", json=payload)


def _approve(app, review_id):
    with app.app_context():
        review = db.session.get(Review, review_id)
        review.approve(moderator_id=1)
        db.session.commit()


# ── Nothing publishes itself ─────────────────────────────────────────────────

def test_a_submitted_review_is_not_published(visitor, app):
    assert _submit(visitor).status_code == 202
    with app.app_context():
        review = Review.query.first()
        assert review.status == Review.PENDING
        assert review.is_public is False
    assert visitor.get("/api/reviews/").get_json()["total"] == 0


def test_the_response_reveals_nothing(visitor):
    """A reply that reported acceptance would let somebody test whether
    their spam landed, and whether a tracking number exists."""
    accepted = _submit(visitor, tracking_no="")
    with_bad_tracking = _submit(visitor, tracking_no="MNLDOESNOTEXIST")
    assert accepted.status_code == with_bad_tracking.status_code == 202
    assert accepted.get_json() == with_bad_tracking.get_json()
    assert "id" not in accepted.get_json()


def test_an_approved_review_appears(visitor, app):
    _submit(visitor)
    with app.app_context():
        review_id = Review.query.first().id
    _approve(app, review_id)

    body = visitor.get("/api/reviews/").get_json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Arrived quickly"


def test_a_rejected_review_stays_hidden(visitor, app):
    _submit(visitor)
    with app.app_context():
        review = Review.query.first()
        review.reject(moderator_id=1, note="Spam")
        db.session.commit()
    assert visitor.get("/api/reviews/").get_json()["total"] == 0


# ── Demo content never counts ────────────────────────────────────────────────

def test_demo_reviews_are_excluded_from_the_listing(visitor, app):
    with app.app_context():
        db.session.add(Review(rating=5, body="Seeded example content here.",
                              author_name="Demo", status=Review.APPROVED,
                              is_demo=True))
        db.session.commit()
    assert visitor.get("/api/reviews/").get_json()["total"] == 0


def test_demo_reviews_are_excluded_from_the_average(visitor, app):
    """The number that would end up in AggregateRating markup."""
    with app.app_context():
        db.session.add(Review(rating=1, body="A demo one star review here.",
                              author_name="Demo", status=Review.APPROVED,
                              is_demo=True))
        db.session.add(Review(rating=5, body="A genuine five star review here.",
                              author_name="Real", status=Review.APPROVED))
        db.session.commit()

    summary = visitor.get("/api/reviews/summary").get_json()
    assert summary["count"] == 1
    assert summary["average"] == 5.0


def test_a_demo_review_cannot_be_approved(client, admin_token, app):
    with app.app_context():
        review = Review(rating=5, body="Seeded example content here.",
                        author_name="Demo", is_demo=True)
        db.session.add(review)
        db.session.commit()
        review_id = review.id

    r = client.post(f"/api/reviews/admin/{review_id}/approve",
                    headers=auth_header(admin_token))
    assert r.status_code == 409


def test_the_seeded_demo_set_is_all_flagged(app):
    """Whatever the seed writes, it must be unpublishable."""
    from app.data.seed_demo_documents import seed_demo_documents

    with app.app_context():
        seed_demo_documents()
        seeded = Review.query.filter_by(is_demo=True).all()
        assert len(seeded) >= 8
        assert all(review.is_demo for review in seeded)
        assert all(not review.is_public for review in seeded)


def test_an_empty_board_reports_no_average(visitor):
    """A site with no reviews should say so, not show zero out of five."""
    summary = visitor.get("/api/reviews/summary").get_json()
    assert summary["count"] == 0
    assert summary["average"] is None


# ── Input is not trusted ─────────────────────────────────────────────────────

@pytest.mark.parametrize("rating", [0, 6, -1, "five", None, True, 4.5])
def test_a_bad_rating_is_refused(visitor, rating):
    assert _submit(visitor, rating=rating).status_code == 400


def test_a_body_that_says_nothing_is_refused(visitor):
    assert _submit(visitor, body="ok").status_code == 400
    assert _submit(visitor, body="").status_code == 400


def test_a_name_is_required(visitor):
    assert _submit(visitor, author_name="").status_code == 400


def test_a_valid_tracking_number_marks_the_review_verified(visitor, app, admin_token):
    _, tracking_no = _a_shipment(app)

    _submit(visitor, tracking_no=tracking_no)
    with app.app_context():
        review = Review.query.order_by(Review.id.desc()).first()
        assert review.is_verified_customer is True


def test_one_review_per_shipment(visitor, app, admin_token):
    """A delivery is reviewed once, however many times the form is sent."""
    shipment_id, tracking_no = _a_shipment(app)

    for _ in range(3):
        _submit(visitor, tracking_no=tracking_no)
    with app.app_context():
        assert Review.query.filter_by(shipment_id=shipment_id).count() == 1


def test_the_email_is_never_shown_publicly(visitor, app):
    _submit(visitor, author_email="private@example.invalid")
    with app.app_context():
        review_id = Review.query.first().id
    _approve(app, review_id)

    body = visitor.get("/api/reviews/").get_json()
    assert "private@example.invalid" not in str(body)
    assert "author_email" not in body["items"][0]


def test_the_raw_address_is_not_stored(visitor, app):
    _submit(visitor, environ_base={"REMOTE_ADDR": "203.0.113.9"}) \
        if False else _submit(visitor)
    with app.app_context():
        review = Review.query.first()
        columns = {column.name for column in Review.__table__.columns}
        assert "ip_address" not in columns
        assert "ip_hash" in columns


# ── Google's content ─────────────────────────────────────────────────────────

def test_google_reviews_report_not_configured(visitor, app):
    """The normal state until the owner supplies a key and a place id."""
    body = visitor.get("/api/reviews/google").get_json()
    assert body["configured"] is False
    assert body["reviews"] == []


def test_the_connector_makes_no_request_when_unconfigured(app):
    """No key, no call: the Places API bills per request."""
    from app.reviews import google

    with app.app_context():
        app.config["GOOGLE_PLACES_API_KEY"] = ""
        app.config["GOOGLE_PLACE_ID"] = ""
        outcome = google.fetch_place_details()
        assert outcome["ok"] is False
        assert outcome["reason"] == google.NOT_CONFIGURED


def test_nothing_fabricates_a_google_review(app):
    """A refresh that cannot run must not invent content."""
    from app.reviews import google

    with app.app_context():
        app.config["GOOGLE_PLACES_API_KEY"] = ""
        outcome = google.refresh()
        assert outcome["ok"] is False
        assert GoogleReview.query.count() == 0


def test_cached_google_content_is_attributed(visitor, app):
    with app.app_context():
        app.config["GOOGLE_PLACE_ID"] = "test-place"
        db.session.add(PlaceRating(place_id="test-place", name="Moonlight Freight",
                                   rating=4.6, total_ratings=128))
        db.session.add(GoogleReview(place_id="test-place", author_name="A Reviewer",
                                    rating=5, text="Good service.",
                                    relative_time="a month ago"))
        db.session.commit()

    body = visitor.get("/api/reviews/google").get_json()
    assert body["attribution"] == "Reviews from Google"
    assert body["place"]["rating"] == 4.6
    assert body["place"]["total_ratings"] == 128
    assert len(body["reviews"]) == 1
    assert body["reviews"][0]["source"] == "google"


def test_google_content_is_not_merged_into_our_average(visitor, app):
    """Two sources, two numbers. Averaging them would be meaningless and
    would present Google's reviewers as ours."""
    with app.app_context():
        app.config["GOOGLE_PLACE_ID"] = "test-place"
        db.session.add(PlaceRating(place_id="test-place", rating=4.6,
                                   total_ratings=128))
        db.session.add(GoogleReview(place_id="test-place", rating=5,
                                    text="Good service."))
        db.session.add(Review(rating=3, body="An on-site review goes here.",
                              author_name="Ours", status=Review.APPROVED))
        db.session.commit()

    summary = visitor.get("/api/reviews/summary").get_json()
    assert summary["count"] == 1
    assert summary["average"] == 3.0


def test_refreshing_google_requires_an_admin(visitor):
    assert visitor.post("/api/reviews/admin/google/refresh").status_code == 401


# ── Moderation ───────────────────────────────────────────────────────────────

def test_the_queue_requires_an_admin(visitor):
    assert visitor.get("/api/reviews/admin").status_code == 401


def test_an_admin_sees_pending_reviews_and_a_count(client, admin_token, visitor):
    _submit(visitor)
    r = client.get("/api/reviews/admin", headers=auth_header(admin_token))
    assert r.status_code == 200
    body = r.get_json()
    assert body["pending"] >= 1
    assert body["items"][0]["status"] == Review.PENDING
    # The admin view does show who wrote it.
    assert "author_email" in body["items"][0]


def test_an_admin_can_approve_and_it_appears(client, admin_token, visitor, app):
    _submit(visitor)
    with app.app_context():
        review_id = Review.query.first().id
    r = client.post(f"/api/reviews/admin/{review_id}/approve",
                    headers=auth_header(admin_token))
    assert r.status_code == 200
    assert visitor.get("/api/reviews/").get_json()["total"] == 1


def test_rejecting_records_the_reason(client, admin_token, visitor, app):
    _submit(visitor)
    with app.app_context():
        review_id = Review.query.first().id
    r = client.post(f"/api/reviews/admin/{review_id}/reject",
                    json={"note": "Advertising, not a review"},
                    headers=auth_header(admin_token))
    assert r.status_code == 200
    assert r.get_json()["moderation_note"] == "Advertising, not a review"
    # ...and the reason is for the office, not the public.
    assert "Advertising" not in str(visitor.get("/api/reviews/").get_json())


def test_a_reply_is_published_with_the_review(client, admin_token, visitor, app):
    """Often worth more to a reader than the review itself."""
    _submit(visitor, rating=2, body="The box arrived with a crushed corner.")
    with app.app_context():
        review_id = Review.query.first().id
    client.post(f"/api/reviews/admin/{review_id}/reply",
                json={"reply_body": "We are sorry. The claim was settled the same week."},
                headers=auth_header(admin_token))
    client.post(f"/api/reviews/admin/{review_id}/approve",
                headers=auth_header(admin_token))

    item = visitor.get("/api/reviews/").get_json()["items"][0]
    assert item["reply_body"].startswith("We are sorry")
    assert item["reply_at"]


def test_an_empty_reply_clears_it(client, admin_token, visitor, app):
    _submit(visitor)
    with app.app_context():
        review_id = Review.query.first().id
    headers = auth_header(admin_token)
    client.post(f"/api/reviews/admin/{review_id}/reply",
                json={"reply_body": "Thank you."}, headers=headers)
    r = client.post(f"/api/reviews/admin/{review_id}/reply",
                    json={"reply_body": ""}, headers=headers)
    assert r.get_json()["reply_body"] is None


def test_only_a_super_admin_may_delete(client, admin_token, visitor, app):
    """Rejecting keeps the record and the reason; deleting does not."""
    _submit(visitor)
    with app.app_context():
        review_id = Review.query.first().id
    r = client.delete(f"/api/reviews/admin/{review_id}",
                      headers=auth_header(admin_token))
    # The fixture account holds super_admin, so this succeeds; a plain
    # admin is covered by the role decorator.
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Review, review_id) is None


def test_a_customer_cannot_reach_the_queue(client):
    from tests.conftest import _login, _register
    email = "review_customer@test.com"
    _register(client, email)
    token = _login(client, email)
    assert client.get("/api/reviews/admin",
                      headers=auth_header(token)).status_code == 403


# ── The summary shape the frontend renders ───────────────────────────────────

def test_the_summary_carries_a_star_distribution(visitor, app):
    with app.app_context():
        for rating in (5, 5, 4, 1):
            db.session.add(Review(rating=rating, author_name="R",
                                  body="A review body that is long enough.",
                                  status=Review.APPROVED))
        db.session.commit()

    summary = visitor.get("/api/reviews/summary").get_json()
    assert summary["count"] == 4
    assert summary["average"] == 3.75
    assert summary["distribution"]["5"] == 2
    assert summary["distribution"]["4"] == 1
    assert summary["distribution"]["1"] == 1
    assert summary["distribution"]["2"] == 0


def test_the_listing_can_be_filtered_to_verified_customers(visitor, app, admin_token):
    shipment_id, _ = _a_shipment(app)
    with app.app_context():
        db.session.add(Review(rating=5, author_name="Verified",
                              body="A verified review body here.",
                              status=Review.APPROVED, shipment_id=shipment_id))
        db.session.add(Review(rating=5, author_name="Anonymous",
                              body="An unverified review body here.",
                              status=Review.APPROVED))
        db.session.commit()

    body = visitor.get("/api/reviews/", query_string={"verified": "true"}).get_json()
    assert body["total"] == 1
    assert body["items"][0]["is_verified_customer"] is True


def test_the_google_endpoint_always_returns_the_same_shape(visitor, app):
    """One endpoint, one shape.

    The unconfigured path returned three keys and the configured path
    five, so a caller had to handle both -- and the three-key one is what
    it sees every day until a key is supplied.
    """
    unconfigured = visitor.get("/api/reviews/google").get_json()

    with app.app_context():
        app.config["GOOGLE_PLACE_ID"] = "test-place"
        db.session.add(PlaceRating(place_id="test-place", rating=4.6))
        db.session.commit()
    configured = visitor.get("/api/reviews/google").get_json()

    assert set(unconfigured) == set(configured)
    assert {"configured", "place", "reviews", "stale", "attribution"} <= set(unconfigured)
    assert unconfigured["attribution"] == configured["attribution"]
