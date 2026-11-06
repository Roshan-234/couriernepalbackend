"""Fetching the company's Google reviews.

A seam, in the same shape as the carrier and SMS connectors: it makes no
request unless a key and a place id are configured, and it returns a
"not configured" result rather than inventing one. Nothing in this
codebase fabricates third-party content.

What the Places API actually gives you is worth knowing before anyone
builds expectations on it:

* **Five reviews, maximum.** There is no paging. Whatever Google decides
  is most relevant is what you get, and it changes between calls.
* **One overall rating**, computed by Google over every review they hold.
  That number is the honest one to display. Averaging the five reviews
  they hand back would produce a different figure and a wrong one.
* **Attribution is required**, and the text may not be edited. The cached
  copy here is refreshed rather than accumulated for that reason.

The call costs money per request, so results are cached for
``GoogleReview.MAX_AGE_HOURS`` and a refresh is a deliberate act — a
scheduled command or an administrator pressing a button, not something
that happens on a page view.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models.review import GoogleReview, PlaceRating

PLACES_ENDPOINT = "https://maps.googleapis.com/maps/api/place/details/json"
FIELDS = "name,rating,user_ratings_total,url,reviews"

NOT_CONFIGURED = "google_reviews_not_configured"


def is_configured():
    config = current_app.config
    return bool(config.get("GOOGLE_PLACES_API_KEY") and config.get("GOOGLE_PLACE_ID"))


def cache_is_fresh(place_id):
    record = PlaceRating.query.filter_by(place_id=place_id).first()
    if not record or not record.fetched_at:
        return False
    age = datetime.utcnow() - record.fetched_at
    return age < timedelta(hours=GoogleReview.MAX_AGE_HOURS)


def fetch_place_details(timeout=10):
    """Call the Places API once. Returns the decoded payload or an error dict.

    The only place in this package that touches the network, and it is
    never reached from a page render.
    """
    if not is_configured():
        return {"ok": False, "reason": NOT_CONFIGURED}

    config = current_app.config
    query = urllib.parse.urlencode({
        "place_id": config["GOOGLE_PLACE_ID"],
        "fields": FIELDS,
        "key": config["GOOGLE_PLACES_API_KEY"],
        # Ask Google for the reviewer's own language rather than a
        # translation, so what is displayed is what was written.
        "reviews_no_translations": "true",
    })
    try:
        with urllib.request.urlopen(f"{PLACES_ENDPOINT}?{query}", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - reported, never raised onward
        current_app.logger.warning("Google Places request failed: %s", error)
        return {"ok": False, "reason": "request_failed", "detail": str(error)[:200]}

    status = payload.get("status")
    if status != "OK":
        # REQUEST_DENIED, OVER_QUERY_LIMIT, INVALID_REQUEST and friends.
        # Reported as-is: a quota problem and a wrong key need different
        # fixes and should not look the same to whoever is debugging.
        return {"ok": False, "reason": status or "unknown_status",
                "detail": payload.get("error_message")}

    return {"ok": True, "result": payload.get("result") or {}}


def refresh(timeout=10):
    """Fetch and replace the cached reviews for the configured place.

    Replace, not append: Google returns a shifting set of five and we are
    displaying their content, not building an archive of it.
    """
    outcome = fetch_place_details(timeout=timeout)
    if not outcome.get("ok"):
        return outcome

    place_id = current_app.config["GOOGLE_PLACE_ID"]
    result = outcome["result"]

    rating = PlaceRating.query.filter_by(place_id=place_id).first()
    if rating is None:
        rating = PlaceRating(place_id=place_id)
        db.session.add(rating)
    rating.name = result.get("name")
    rating.rating = result.get("rating")
    rating.total_ratings = result.get("user_ratings_total")
    rating.url = result.get("url")
    rating.fetched_at = datetime.utcnow()

    GoogleReview.query.filter_by(place_id=place_id).delete(synchronize_session=False)

    stored = 0
    for review in result.get("reviews") or []:
        posted = review.get("time")
        db.session.add(GoogleReview(
            place_id=place_id,
            external_id=str(review.get("author_url") or "")[:200] or None,
            author_name=review.get("author_name"),
            author_url=review.get("author_url"),
            profile_photo_url=review.get("profile_photo_url"),
            rating=review.get("rating"),
            text=review.get("text"),
            relative_time=review.get("relative_time_description"),
            original_language=review.get("original_language") or review.get("language"),
            posted_at=datetime.utcfromtimestamp(posted) if posted else None,
        ))
        stored += 1

    db.session.commit()
    return {"ok": True, "reviews": stored, "rating": rating.rating,
            "total_ratings": rating.total_ratings}


def cached():
    """What we hold for the configured place, without calling anybody."""
    config = current_app.config
    place_id = config.get("GOOGLE_PLACE_ID")
    if not place_id:
        return {"configured": False, "reviews": [], "place": None}

    place = PlaceRating.query.filter_by(place_id=place_id).first()
    reviews = (GoogleReview.query.filter_by(place_id=place_id)
               .order_by(GoogleReview.posted_at.desc().nullslast(),
                         GoogleReview.id.desc()).all())
    return {
        "configured": is_configured(),
        "place": place.to_dict() if place else None,
        "reviews": [review.to_dict() for review in reviews],
        "stale": not cache_is_fresh(place_id),
        # Required wherever this content is shown.
        "attribution": "Reviews from Google",
    }
