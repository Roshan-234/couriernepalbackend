"""The published FAQ set.

Two things shaped these answers.

**They are international-first.** The company's business is cargo and
parcels moving between Nepal and the diaspora — the Gulf, the UK and
Europe, North America, East Asia — with domestic delivery as the feeder
network. The previous default set led with domestic coverage and read like
a local courier's.

**They do not make claims nobody has verified.** The set they replace
promised specific payment methods (eSewa, Khalti, card) that are inactive
seams in this system, and quoted a support address that is not the one the
company uses. An FAQ is the page a customer reads *before* they decide to
trust you with a parcel; a wrong answer there is worse than no answer.

So anything that depends on a commercial fact — transit times, prices,
which payment methods are actually live, insurance limits — either is not
stated or defers to the team. Those are listed in CONTENT-TO-CONFIRM.md
and a twenty-year-old business can answer every one of them from memory.
Replace the hedged wording as the answers come in; the FAQ page renders
straight from these rows and is marked up as `FAQPage`, so a precise
answer earns an expandable result in Google and a vague one does not.
"""

from app.extensions import db

#: question, answer, category, display order
FAQS = [
    ("Which countries do you ship to from Nepal?",
     "We handle international air and sea cargo, express courier, excess "
     "baggage and personal-effects relocation out of Kathmandu, and inbound "
     "shipments back to Nepal. Tell us the destination and what you are "
     "sending and we will confirm the service, the documents needed and the "
     "cost before anything is collected.",
     "international", 1),

    ("How long will my shipment take?",
     "It depends on the destination, the service and customs at both ends. "
     "Air freight and express are the fastest; sea cargo is the most "
     "economical for weight and volume. We give you a transit estimate when "
     "we quote, and your tracking number shows each step once the parcel is "
     "moving.",
     "international", 2),

    ("How is the cost worked out?",
     "On the greater of actual weight and volumetric weight — length x width "
     "x height in centimetres, divided by 5000 — plus the route, the service "
     "level and any surcharges that apply, such as fragile handling or "
     "insurance. Bulky light items are charged on their size, which is why a "
     "quote needs dimensions as well as weight.",
     "pricing", 3),

    ("Can you handle customs paperwork?",
     "Yes. We prepare the export documentation for shipments leaving Nepal "
     "and advise on what the destination country requires. Rules differ by "
     "country and change without much notice, so confirm the current "
     "requirements with our team when you book rather than relying on a "
     "previous shipment.",
     "international", 4),

    ("What can I not send?",
     "Dangerous goods, restricted items and anything prohibited by the "
     "airline, the destination country or Nepali export rules. Batteries, "
     "aerosols, liquids, foodstuffs, plant and animal products and medicines "
     "are the ones that most often cause a problem. Our prohibited items "
     "page covers the common cases, and our team will check anything you are "
     "unsure about before you pack it.",
     "general", 5),

    ("How do I track my shipment?",
     "Enter your tracking number on the tracking page. You do not need an "
     "account, and the person receiving the parcel can follow it with the "
     "same number.",
     "tracking", 6),

    ("Do you collect from my address?",
     "Yes, we arrange pickups in the Kathmandu valley and through our branch "
     "network elsewhere in Nepal. Book a pickup through your account or call "
     "the office and we will agree a time.",
     "domestic", 7),

    ("Do you work with businesses and online sellers?",
     "Yes. Regular shippers get an account, agreed rates and an API for "
     "booking and tracking from their own system. Talk to us about volumes "
     "and we will put terms in place.",
     "business", 8),

    ("Is my shipment insured?",
     "Insurance is optional and based on the declared value of the goods, so "
     "declare the real value when you book. Ask our team for the cover "
     "available on your route and service before the parcel is collected.",
     "general", 9),

    ("Something has gone wrong with my shipment. What do I do?",
     "Contact us with your tracking number as soon as you can. If a parcel "
     "is delayed, damaged or missing, you can raise a claim through your "
     "account and we will come back to you with what we find.",
     "general", 10),

    ("How do I get in touch?",
     "Call +977 1-5922458 or email moonlightfreight@gmail.com. The head "
     "office is at Nayabazar, Kathmandu.",
     "general", 11),
]


def seed_faqs():
    """Insert any FAQ that is not already present, keyed on the question.

    Idempotent, and never edits an existing row: once the office has
    reworded an answer, that wording wins over this file.
    """
    from app.models.faq import FAQ

    created = 0
    for question, answer, category, order in FAQS:
        if FAQ.query.filter_by(question=question).first():
            continue
        db.session.add(FAQ(question=question, answer=answer,
                           category=category, order=order, is_active=True))
        created += 1
    db.session.commit()
    return {"created": created, "total": len(FAQS)}
