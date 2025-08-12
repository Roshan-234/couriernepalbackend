from app.extensions import db

class PricingRule(db.Model):
    __tablename__ = "pricing_rules"

    id = db.Column(db.Integer, primary_key=True)
    service_type = db.Column(db.String(50), nullable=False)
    shipment_type = db.Column(db.String(50), nullable=False)
    origin_country = db.Column(db.String(100), nullable=False)
    destination_country = db.Column(db.String(100), nullable=False)
    base_price = db.Column(db.Float, nullable=False)
    price_per_kg = db.Column(db.Float, nullable=False)
    min_weight = db.Column(db.Float, default=0.0)
    max_weight = db.Column(db.Float)
    insurance_percentage = db.Column(db.Float, default=0.0)
    tax_percentage = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)

    def calculate_price(self, weight, declared_value=0):
        if weight < self.min_weight or (self.max_weight and weight > self.max_weight):
            raise ValueError("Weight out of allowed range")

        shipping_cost = self.base_price + (weight * self.price_per_kg)
        insurance_cost = (declared_value * self.insurance_percentage / 100) if declared_value > 0 else 0
        tax = ((shipping_cost + insurance_cost) * self.tax_percentage / 100)
        
        return {
            "shipping_cost": shipping_cost,
            "insurance_cost": insurance_cost,
            "tax": tax,
            "total_cost": shipping_cost + insurance_cost + tax
        }

    def to_dict(self):
        return {
            "id": self.id,
            "service_type": self.service_type,
            "shipment_type": self.shipment_type,
            "origin_country": self.origin_country,
            "destination_country": self.destination_country,
            "base_price": self.base_price,
            "price_per_kg": self.price_per_kg,
            "min_weight": self.min_weight,
            "max_weight": self.max_weight,
            "insurance_percentage": self.insurance_percentage,
            "tax_percentage": self.tax_percentage,
            "is_active": self.is_active
        }
