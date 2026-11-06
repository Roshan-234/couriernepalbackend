"""Reviews: what customers wrote, and what Google holds.

The public endpoints serve approved, non-demo reviews and the cached
Google set. The admin endpoints are the moderation queue.

The rule that shapes all of it: **a review is not published until a person
approves it.** Submission returns the same acknowledgement either way and
never echoes the review back as live content, so nobody can use the
endpoint to check whether their spam landed.
"""

from datetime import datetime

from flask import request
from flask_restx import Namespace, Resource, fields
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import func
from werkzeug.exceptions import BadRequest

from app.auth.utils import require_roles
from app.extensions import db, limiter
from app.models.review import GoogleReview, PlaceRating, Review
from app.models.shipment import Shipment
from app.models.user import User
from app.net import rate_limit_key
from app.reviews import google
from app.validation import json_object, pagination, text
from app.visitor import request_fingerprint

review_ns = Namespace('reviews', description='Customer reviews')

review_input = review_ns.model('ReviewInput', {
    'rating': fields.Integer(required=True, min=1, max=5),
    'title': fields.String(),
    'body': fields.String(required=True),
    'author_name': fields.String(required=True, description='Shown publicly'),
    'author_email': fields.String(description='Never shown publicly'),
    'language': fields.String(enum=['en', 'ne'], default='en'),
    'tracking_no': fields.String(
        description='Links the review to a real delivery, which is what '
                    '"verified customer" means here'),
})


def _current_user():
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        return db.session.get(User, int(identity)) if identity else None
    except Exception:
        return None


def _public_query():
    """Approved, real reviews. The one definition, used everywhere."""
    return Review.query.filter(Review.status == Review.APPROVED,
                               Review.is_demo.is_(False))


def _summary():
    rating, count = (db.session.query(func.avg(Review.rating),
                                      func.count(Review.id))
                     .filter(Review.status == Review.APPROVED,
                             Review.is_demo.is_(False))
                     .one())
    distribution = dict(
        db.session.query(Review.rating, func.count(Review.id))
        .filter(Review.status == Review.APPROVED, Review.is_demo.is_(False))
        .group_by(Review.rating).all())
    return {
        'average': round(float(rating), 2) if rating else None,
        'count': int(count or 0),
        'distribution': {str(star): int(distribution.get(star, 0))
                         for star in range(1, 6)},
    }


# ── Public ───────────────────────────────────────────────────────────────────

@review_ns.route('/')
class ReviewList(Resource):
    @review_ns.doc('list_reviews')
    def get(self):
        """Approved reviews, newest first. Paginated.

        Demo rows never appear here. They exist so the screens can be
        built and shown; counting them would make the rating a fiction.
        """
        page, per_page = pagination()
        query = _public_query()

        if rating := request.args.get('rating'):
            try:
                query = query.filter(Review.rating == int(rating))
            except ValueError:
                raise BadRequest('rating must be a whole number from 1 to 5')
        if request.args.get('verified') in ('1', 'true'):
            query = query.filter(Review.shipment_id.isnot(None))

        result = (query.order_by(Review.created_at.desc(), Review.id.desc())
                  .paginate(page=page, per_page=per_page, error_out=False))
        return {
            'items': [review.to_dict() for review in result.items],
            'total': result.total,
            'page': page,
            'per_page': per_page,
            'pages': result.pages,
            'summary': _summary(),
        }, 200

    @review_ns.doc('submit_review')
    @review_ns.expect(review_input)
    @review_ns.response(202, 'Received, and awaiting moderation')
    @limiter.limit('5 per hour', key_func=rate_limit_key)
    def post(self):
        """Submit a review.

        Always answers 202 with the same body, whatever happens next. The
        reply does not say whether the review was accepted, whether the
        tracking number matched, or whether this person has reviewed
        before — an endpoint that reports those things is a way to test
        tracking numbers and to confirm spam got through.
        """
        data = json_object()

        rating = data.get('rating')
        if not isinstance(rating, int) or isinstance(rating, bool) \
                or not Review.MIN_RATING <= rating <= Review.MAX_RATING:
            raise BadRequest('Give a rating from 1 to 5')

        body = text(data.get('body'), 'body', 4000, required=True)
        if len(body) < 10:
            raise BadRequest('Tell us a little more than that')

        author_name = text(data.get('author_name'), 'author_name', 120, required=True)
        title = text(data.get('title', ''), 'title', 150)
        email = text(data.get('author_email', ''), 'author_email', 200)
        language = data.get('language') if data.get('language') in ('en', 'ne') else 'en'

        user = _current_user()
        evidence = request_fingerprint()

        # A tracking number ties the review to a real delivery. It is
        # checked quietly: an invalid one produces an unverified review,
        # not an error that confirms which numbers exist.
        shipment = None
        tracking_no = (data.get('tracking_no') or '').strip()
        if tracking_no:
            shipment = Shipment.query.filter_by(tracking_no=tracking_no).first()

        already = None
        if shipment is not None:
            already = Review.query.filter_by(shipment_id=shipment.id).first()

        if already is None:
            db.session.add(Review(
                rating=rating, title=title or None, body=body,
                author_name=author_name, author_email=email or None,
                author_country=evidence.get('country'),
                language=language,
                user_id=user.id if user else None,
                shipment_id=shipment.id if shipment is not None else None,
                status=Review.PENDING,
                ip_hash=evidence.get('ip_hash'),
            ))
            db.session.commit()

        return {
            'status': 'received',
            'message': ('Thank you. Your review has been sent to our team and '
                        'will appear once it has been read.'),
        }, 202


@review_ns.route('/summary')
class ReviewSummary(Resource):
    @review_ns.doc('review_summary')
    def get(self):
        """Average, count and star distribution over approved reviews.

        `average` is null when there is nothing approved yet. A site with
        no reviews should say so rather than display a zero out of five.
        """
        return _summary(), 200


@review_ns.route('/google')
class GoogleReviews(Resource):
    @review_ns.doc('google_reviews')
    def get(self):
        """The cached Google reviews and the overall Google rating.

        Served from cache and never fetched on a page view: the Places
        API charges per call. `configured` is false when no key or place
        id is set, which is the normal state until the owner supplies
        them, and the frontend should then show nothing rather than an
        empty widget.

        Attribution to Google is required wherever this is displayed and
        is returned alongside the content.
        """
        return google.cached(), 200


# ── Administration ───────────────────────────────────────────────────────────

@review_ns.route('/admin')
class ReviewQueue(Resource):
    @review_ns.doc('review_queue', security='Bearer')
    @require_roles('admin', 'super_admin')
    def get(self):
        """Every review, including pending and demo. Filter on `status`."""
        page, per_page = pagination()
        query = Review.query

        if status := request.args.get('status'):
            if status not in Review.STATUSES:
                raise BadRequest('Unknown review status')
            query = query.filter(Review.status == status)
        if request.args.get('include_demo') in ('0', 'false'):
            query = query.filter(Review.is_demo.is_(False))

        result = (query.order_by(Review.created_at.desc(), Review.id.desc())
                  .paginate(page=page, per_page=per_page, error_out=False))
        pending = Review.query.filter_by(status=Review.PENDING).count()
        return {
            'items': [review.to_dict(admin=True) for review in result.items],
            'total': result.total,
            'page': page,
            'per_page': per_page,
            'pages': result.pages,
            'pending': pending,
            'summary': _summary(),
        }, 200


def _load(review_id):
    review = db.session.get(Review, review_id)
    if not review:
        review_ns.abort(404, 'Review not found')
    return review


@review_ns.route('/admin/<int:review_id>/approve')
class ReviewApprove(Resource):
    @review_ns.doc('approve_review', security='Bearer')
    @require_roles('admin', 'super_admin')
    def post(self, review_id):
        """Publish a review.

        A demo review cannot be approved into the public listing: it would
        still be excluded by `is_public`, so refusing here is clearer than
        appearing to work and then doing nothing.
        """
        review = _load(review_id)
        if review.is_demo:
            review_ns.abort(409, 'Demo reviews are never published')
        review.approve(int(get_jwt_identity()))
        db.session.commit()
        return review.to_dict(admin=True), 200


@review_ns.route('/admin/<int:review_id>/reject')
class ReviewReject(Resource):
    @review_ns.doc('reject_review', security='Bearer')
    @require_roles('admin', 'super_admin')
    def post(self, review_id):
        """Reject a review, with a note for the office."""
        review = _load(review_id)
        data = json_object()
        review.reject(int(get_jwt_identity()),
                      text(data.get('note', ''), 'note', 300))
        db.session.commit()
        return review.to_dict(admin=True), 200


@review_ns.route('/admin/<int:review_id>/reply')
class ReviewReply(Resource):
    @review_ns.doc('reply_to_review', security='Bearer')
    @require_roles('admin', 'super_admin')
    def post(self, review_id):
        """Answer a review publicly.

        Often worth more to a reader than the review itself, particularly
        on a complaint. Sending an empty body clears an existing reply.
        """
        review = _load(review_id)
        data = json_object()
        reply = text(data.get('reply_body', ''), 'reply_body', 2000)
        if reply:
            review.reply_body = reply
            review.reply_at = datetime.utcnow()
            review.reply_by = int(get_jwt_identity())
        else:
            review.reply_body = None
            review.reply_at = None
            review.reply_by = None
        db.session.commit()
        return review.to_dict(admin=True), 200


@review_ns.route('/admin/<int:review_id>')
class ReviewItem(Resource):
    @review_ns.doc('delete_review', security='Bearer')
    @require_roles('super_admin')
    def delete(self, review_id):
        """Remove a review entirely. Super administrators only.

        Rejecting keeps the record and the reason; deleting does not, so
        it is reserved for content that should not be retained at all.
        """
        review = _load(review_id)
        db.session.delete(review)
        db.session.commit()
        return {'deleted': review_id}, 200


@review_ns.route('/admin/google/refresh')
class GoogleRefresh(Resource):
    @review_ns.doc('refresh_google_reviews', security='Bearer')
    @require_roles('admin', 'super_admin')
    @limiter.limit('10 per hour', key_func=rate_limit_key)
    def post(self):
        """Fetch the Google reviews again.

        A deliberate act, and rate limited, because the Places API bills
        per request. Returns the connector's own reason when it is not
        configured rather than pretending to have refreshed.
        """
        outcome = google.refresh()
        return outcome, (200 if outcome.get('ok') else 409)
