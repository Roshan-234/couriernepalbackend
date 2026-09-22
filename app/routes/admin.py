from flask import Blueprint, jsonify
from datetime import datetime, timedelta
from sqlalchemy import func, case
from app.extensions import db
from app.models.shipment import Shipment
from app.models.invoice import Invoice
from app.models.user import User
from app.models.role import Role
from app.auth.utils import admin_required

bp = Blueprint('admin', __name__)


def _pct_change(current, previous):
    """Percent change, guarding division by zero."""
    if previous and previous > 0:
        return round(((current - previous) / previous) * 100, 1)
    return 0.0


def _revenue_between(start, end=None):
    """Sum of non-cancelled invoice totals in a date range."""
    q = db.session.query(func.coalesce(func.sum(Invoice.total_amount), 0.0)).filter(
        Invoice.status != 'cancelled'
    )
    if end is None:
        q = q.filter(func.date(Invoice.created_at) >= start)
    else:
        q = q.filter(func.date(Invoice.created_at).between(start, end))
    return float(q.scalar() or 0.0)


def _is_international():
    """SQL expression: 1 when shipment crosses a border, else 0."""
    return case(
        (
            (Shipment.destination_country.isnot(None))
            & (Shipment.destination_country != Shipment.origin_country),
            1,
        ),
        else_=0,
    )


@bp.route('/dashboard/stats', methods=['GET'])
@admin_required
def get_dashboard_stats():
    today = datetime.utcnow().date()
    thirty_days_ago = today - timedelta(days=30)
    previous_thirty_days = thirty_days_ago - timedelta(days=30)

    # Shipments
    current_shipments = Shipment.query.filter(
        func.date(Shipment.created_at) >= thirty_days_ago
    ).count()
    previous_shipments = Shipment.query.filter(
        func.date(Shipment.created_at).between(previous_thirty_days, thirty_days_ago)
    ).count()

    # Revenue (from issued/paid invoices)
    current_revenue = _revenue_between(thirty_days_ago)
    previous_revenue = _revenue_between(previous_thirty_days, thirty_days_ago)

    # Customers (users holding the 'customer' role)
    customer_q = User.query.join(User.roles).filter(Role.name == 'customer')
    current_customers = customer_q.filter(
        func.date(User.created_at) >= thirty_days_ago
    ).count()
    previous_customers = customer_q.filter(
        func.date(User.created_at).between(previous_thirty_days, thirty_days_ago)
    ).count()

    # Average delivery time in hours for delivered shipments (SQLite-safe).
    # julianday() returns fractional days; * 24 -> hours.
    avg_delivery_time = db.session.query(
        func.avg((func.julianday(Shipment.updated_at) - func.julianday(Shipment.created_at)) * 24.0)
    ).filter(
        Shipment.status == 'delivered',
        func.date(Shipment.created_at) >= thirty_days_ago,
    ).scalar() or 0
    previous_avg_delivery_time = db.session.query(
        func.avg((func.julianday(Shipment.updated_at) - func.julianday(Shipment.created_at)) * 24.0)
    ).filter(
        Shipment.status == 'delivered',
        func.date(Shipment.created_at).between(previous_thirty_days, thirty_days_ago),
    ).scalar() or 0

    return jsonify({
        'totalShipments': current_shipments,
        'shipmentChange': _pct_change(current_shipments, previous_shipments),
        'revenue': round(current_revenue, 2),
        'revenueChange': _pct_change(current_revenue, previous_revenue),
        'activeCustomers': current_customers,
        'customerChange': _pct_change(current_customers, previous_customers),
        'avgDeliveryTime': round(float(avg_delivery_time), 1),
        'deliveryTimeChange': _pct_change(float(avg_delivery_time), float(previous_avg_delivery_time)),
    })


@bp.route('/dashboard/shipment-trends', methods=['GET'])
@admin_required
def get_shipment_trends():
    today = datetime.utcnow().date()
    thirty_days_ago = today - timedelta(days=30)

    rows = db.session.query(
        func.date(Shipment.created_at).label('date'),
        func.sum(case((_is_international() == 1, 1), else_=0)).label('intl'),
        func.sum(case((_is_international() == 0, 1), else_=0)).label('dom'),
    ).filter(
        func.date(Shipment.created_at) >= thirty_days_ago
    ).group_by(
        func.date(Shipment.created_at)
    ).all()

    dates = [(today - timedelta(days=x)).strftime('%Y-%m-%d') for x in range(30)]
    domestic_counts = [0] * 30
    international_counts = [0] * 30

    for row in rows:
        # row.date is a string 'YYYY-MM-DD' from SQLite
        try:
            d = datetime.strptime(str(row.date), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            continue
        idx = (today - d).days
        if 0 <= idx < 30:
            domestic_counts[idx] = int(row.dom or 0)
            international_counts[idx] = int(row.intl or 0)

    return jsonify({
        'labels': dates[::-1],
        'domestic': domestic_counts[::-1],
        'international': international_counts[::-1],
    })


@bp.route('/dashboard/revenue-analytics', methods=['GET'])
@admin_required
def get_revenue_analytics():
    today = datetime.utcnow().date()
    cursor = today.replace(day=1)

    revenue_data = []
    expenses_data = []
    labels = []

    # Walk back 12 months
    months = []
    for _ in range(12):
        month_start = cursor
        # last day of this month
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        months.append((month_start, month_end))
        cursor = (month_start - timedelta(days=1)).replace(day=1)

    for month_start, month_end in reversed(months):
        revenue = _revenue_between(month_start, month_end)
        expenses = revenue * 0.6  # estimated operating cost
        revenue_data.append(round(revenue, 2))
        expenses_data.append(round(expenses, 2))
        labels.append(month_start.strftime('%B %Y'))

    profit_data = [round(rev - exp, 2) for rev, exp in zip(revenue_data, expenses_data)]

    return jsonify({
        'labels': labels,
        'revenue': revenue_data,
        'expenses': expenses_data,
        'profit': profit_data,
    })
