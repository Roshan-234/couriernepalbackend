from flask import Blueprint, jsonify
from datetime import datetime, timedelta
from sqlalchemy import func, desc
from app.extensions import db
from app.models.shipment import Shipment
from app.models.user import User
from app.auth.utils import admin_required

bp = Blueprint('admin', __name__)

@bp.route('/dashboard/stats', methods=['GET'])
@admin_required
def get_dashboard_stats():
    # Get today's date and dates for comparison
    today = datetime.utcnow().date()
    thirty_days_ago = today - timedelta(days=30)
    previous_thirty_days = thirty_days_ago - timedelta(days=30)
    
    # Current period stats
    current_shipments = Shipment.query.filter(
        func.date(Shipment.created_at) >= thirty_days_ago
    ).count()
    
    # Previous period stats for comparison
    previous_shipments = Shipment.query.filter(
        func.date(Shipment.created_at).between(previous_thirty_days, thirty_days_ago)
    ).count()
    
    # Calculate changes
    shipment_change = (
        ((current_shipments - previous_shipments) / previous_shipments) * 100
        if previous_shipments > 0 else 0
    )
    
    # Get revenue stats
    current_revenue = db.session.query(
        func.sum(Shipment.cost)
    ).filter(
        func.date(Shipment.created_at) >= thirty_days_ago
    ).scalar() or 0
    
    previous_revenue = db.session.query(
        func.sum(Shipment.cost)
    ).filter(
        func.date(Shipment.created_at).between(previous_thirty_days, thirty_days_ago)
    ).scalar() or 0
    
    revenue_change = (
        ((current_revenue - previous_revenue) / previous_revenue) * 100
        if previous_revenue > 0 else 0
    )
    
    # Get customer stats
    current_customers = User.query.filter(
        User.role_id == 2,  # Assuming 2 is customer role
        func.date(User.created_at) >= thirty_days_ago
    ).count()
    
    previous_customers = User.query.filter(
        User.role_id == 2,
        func.date(User.created_at).between(previous_thirty_days, thirty_days_ago)
    ).count()
    
    customer_change = (
        ((current_customers - previous_customers) / previous_customers) * 100
        if previous_customers > 0 else 0
    )
    
    # Calculate average delivery time
    avg_delivery_time = db.session.query(
        func.avg(
            func.extract('epoch', Shipment.delivered_at) - 
            func.extract('epoch', Shipment.created_at)
        ) / 3600  # Convert to hours
    ).filter(
        Shipment.status == 'delivered',
        func.date(Shipment.created_at) >= thirty_days_ago
    ).scalar() or 0
    
    previous_avg_delivery_time = db.session.query(
        func.avg(
            func.extract('epoch', Shipment.delivered_at) - 
            func.extract('epoch', Shipment.created_at)
        ) / 3600
    ).filter(
        Shipment.status == 'delivered',
        func.date(Shipment.created_at).between(previous_thirty_days, thirty_days_ago)
    ).scalar() or 0
    
    delivery_time_change = (
        ((avg_delivery_time - previous_avg_delivery_time) / previous_avg_delivery_time) * 100
        if previous_avg_delivery_time > 0 else 0
    )
    
    return jsonify({
        'totalShipments': current_shipments,
        'shipmentChange': round(shipment_change, 1),
        'revenue': round(current_revenue, 2),
        'revenueChange': round(revenue_change, 1),
        'activeCustomers': current_customers,
        'customerChange': round(customer_change, 1),
        'avgDeliveryTime': round(avg_delivery_time, 1),
        'deliveryTimeChange': round(delivery_time_change, 1)
    })

@bp.route('/dashboard/shipment-trends', methods=['GET'])
@admin_required
def get_shipment_trends():
    # Get daily shipment counts for the last 30 days
    today = datetime.utcnow().date()
    thirty_days_ago = today - timedelta(days=30)
    
    domestic_shipments = db.session.query(
        func.date(Shipment.created_at).label('date'),
        func.count().label('count')
    ).filter(
        func.date(Shipment.created_at) >= thirty_days_ago,
        Shipment.shipment_type == 'domestic'
    ).group_by(
        func.date(Shipment.created_at)
    ).order_by(
        'date'
    ).all()
    
    international_shipments = db.session.query(
        func.date(Shipment.created_at).label('date'),
        func.count().label('count')
    ).filter(
        func.date(Shipment.created_at) >= thirty_days_ago,
        Shipment.shipment_type == 'international'
    ).group_by(
        func.date(Shipment.created_at)
    ).order_by(
        'date'
    ).all()
    
    # Create date labels and initialize counts
    dates = [(today - timedelta(days=x)).strftime('%Y-%m-%d') for x in range(30)]
    domestic_counts = [0] * 30
    international_counts = [0] * 30
    
    # Fill in actual counts
    for shipment in domestic_shipments:
        day_index = (today - shipment.date).days
        if 0 <= day_index < 30:
            domestic_counts[day_index] = shipment.count
    
    for shipment in international_shipments:
        day_index = (today - shipment.date).days
        if 0 <= day_index < 30:
            international_counts[day_index] = shipment.count
    
    return jsonify({
        'labels': dates[::-1],  # Reverse to show oldest to newest
        'domestic': domestic_counts[::-1],
        'international': international_counts[::-1]
    })

@bp.route('/dashboard/revenue-analytics', methods=['GET'])
@admin_required
def get_revenue_analytics():
    # Get monthly revenue data for the last 12 months
    today = datetime.utcnow().date()
    
    revenue_data = []
    expenses_data = []
    labels = []
    
    for i in range(11, -1, -1):
        start_date = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        end_date = today if i == 0 else start_date.replace(day=1) - timedelta(days=1)
        
        # Get revenue for the month
        revenue = db.session.query(
            func.sum(Shipment.cost)
        ).filter(
            func.date(Shipment.created_at).between(start_date, end_date)
        ).scalar() or 0
        
        # Estimate expenses (example: 60% of revenue)
        expenses = revenue * 0.6
        
        revenue_data.append(round(revenue, 2))
        expenses_data.append(round(expenses, 2))
        labels.append(start_date.strftime('%B %Y'))
        
        today = start_date
    
    # Calculate profit
    profit_data = [round(rev - exp, 2) for rev, exp in zip(revenue_data, expenses_data)]
    
    return jsonify({
        'labels': labels,
        'revenue': revenue_data,
        'expenses': expenses_data,
        'profit': profit_data
    })
