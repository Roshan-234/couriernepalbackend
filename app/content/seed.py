"""Insert starter editorial content once; preserve administrator edits."""
from app.extensions import db
from app.models.site_page import SitePage

DEFAULTS = [
    ('/', 'Courier services in Nepal', 'YOUR WORLD, DELIVERED', 'Happiness, delivered.',
     'Across Nepal or across the world, every delivery matters. We help your parcels move forward, so you can too.',
     [('Book your shipment', 'Tell us where it is going and what you are sending.'), ('Prepare for pickup', 'Pack your parcel and coordinate with our team.'), ('Follow the journey', 'Use your tracking number to check the latest updates.')]),
    ('/about', 'About Moonlight Freight', 'ROOTED IN NEPAL. CONNECTED TO THE WORLD.', 'Behind every parcel, there is a possibility.',
     'Courier Nepal, operated by Moonlight Freight Pvt. Ltd., helps people and businesses move documents, parcels and cargo from Nepal.',
     [('Care', 'Every shipment deserves attention, from preparation to delivery.'), ('Clarity', 'Useful information, clear next steps and someone to speak to.'), ('Connection', 'Helping people and businesses stay connected through delivery.')]),
    ('/services', 'Courier and freight services', 'THE RIGHT WAY TO GET THERE', 'Logistics for every chapter.',
     'A single parcel or a growing business. Find the support that fits your next delivery.', []),
    ('/contact', 'Contact our shipping team', 'REAL PEOPLE. READY TO HELP.', 'Let us talk delivery.',
     'A shipping question, a business idea, or a parcel that needs attention. Our team is here for you.',
     [('Visit us', 'Nayabazar, Kathmandu, Nepal'), ('Call us', '+977 1-5922458'), ('Email us', 'moonlightfreight@gmail.com'), ('Office hours', 'Contact our team to confirm availability.')]),
    ('/pricing', 'Shipping rates and quotes', 'PLAN YOUR NEXT DELIVERY', 'A rate for your route.',
     'Choose an available service to estimate shipping costs using our current rates. Confirm final charges with our team before booking.', []),
    ('/tracking', 'Track your shipment', 'EVERY STEP, A LITTLE CLOSER', 'Follow your delivery.',
     'One tracking number. The latest updates on your parcel journey.', []),
    ('/blog', 'Shipping news and advice', 'FROM OUR TEAM', 'Helpful notes for your next delivery.',
     'Shipping guidance and updates from Moonlight Freight.', []),
    ('/privacy', 'Privacy and analytics', 'YOUR INFORMATION', 'Privacy matters.',
     'We use the information you provide to manage your account, respond to enquiries and coordinate shipments.',
     [('Shipping information', 'Contact and delivery details are used to provide the requested service. Contact our team with questions about your personal information.'),
      ('Optional analytics', 'Google Analytics is loaded only after you choose to allow analytics. Account pages, tracking results, query strings and form contents are excluded from our analytics events.'),
      ('Essential cookies', 'Authentication and CSRF cookies support secure account access. Your analytics preference is stored locally in your browser.')]),
]
SERVICES = {
    'domestic': ('Domestic delivery', 'Closer to the people who matter.', 'Send documents and parcels across Nepal with a team that understands the local journey.'),
    'international': ('International shipping', 'Your world has no borders.', 'Connect with people and businesses overseas, with support from packing to delivery.'),
    'ecommerce': ('E-commerce logistics', 'You grow. We help you deliver.', 'Delivery coordination built around your orders and business needs.'),
    'warehouse': ('Warehouse solutions', 'Space for your next chapter.', 'Discuss storage and fulfilment options that fit your stock and schedule.'),
    'packing': ('Packing services', 'Care begins before the journey.', 'Get help preparing your items for transport and protecting them in transit.'),
    'express': ('Express delivery', 'When every moment matters.', 'Ask our team about available priority services for time-sensitive deliveries.'),
}


def seed_content():
    rows = list(DEFAULTS)
    for slug, (title, heading, intro) in SERVICES.items():
        rows.append(('/services/' + slug, title, 'SHIPPING WITH CARE', heading, intro,
                     [('Plan your delivery', 'Contact our team to confirm service availability, requirements and timing for your route.'),
                      ('Stay informed', 'Track your shipment online and contact us when you need help.')]))
    existing = {path for (path,) in db.session.query(SitePage.path).all()}
    for path, title, eyebrow, heading, intro, sections in rows:
        if path not in existing:
            db.session.add(SitePage(path=path, title=title, description=intro[:320], eyebrow=eyebrow,
                heading=heading, intro=intro, sections=[{'heading': h, 'body': b} for h, b in sections]))
    db.session.commit()
