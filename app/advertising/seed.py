"""Starter data for the advertising module.

Run with ``flask seed-ads``. Two properties hold and both are load-bearing:

**Idempotent.** Every row is keyed on its natural ``code`` and an existing
one is left exactly as it is. Marketing will have edited the copy; a seed
that overwrote it on the next deploy would be a bug with a wide blast
radius.

**Nothing goes live.** Every campaign is ``draft``, every creative and
notice is inactive, and no campaign carries dates. Running this on
production must never put an advert in front of a customer — somebody has
to write the real offer, set the real dates and press activate. The
placements are the exception and are seeded active, because a slot is a hole
in a page rather than something that renders.

**On the Nepali copy.** ``_ne`` is filled only where the Nepali is right,
and left empty everywhere else: the model falls back to English, and an
empty field costs nothing while wrong Devanagari in a customer-facing offer
costs trust. The fields that are filled are short festival headlines. See
the ``NEEDS_NATIVE_REVIEW`` list at the bottom for what a Nepali speaker
should look at before any of this is published.
"""

from app.extensions import db
from app.models.advertising import AdCampaign, AdCreative, AdPlacement, SiteNotice

# ── Placements ───────────────────────────────────────────────────────────────

#: The slots the site actually has. ``max_creatives`` is the honest capacity
#: of the slot, not an ambition: a hero shows one thing, an offers row shows
#: three cards.
#:
#: code, name, description, width, height, max_creatives
PLACEMENTS = [
    ("home_hero", "Home hero banner",
     "Full-width panel directly under the header on the home page. One offer at a "
     "time, the largest thing on the site, and the only slot above the fold.",
     1600, 600, 1),
    ("home_strip", "Home announcement strip",
     "Slim strip beneath the hero. Used for a single line with a link — a cut-off "
     "date, a seasonal rate, a service change.",
     1200, 160, 1),
    ("home_offers", "Home offers row",
     "Row of promotion cards further down the home page. Holds up to three, so a "
     "festival offer, a corridor rate and a service promotion can run together.",
     600, 400, 3),
    ("pricing_banner", "Pricing page banner",
     "Above the rate calculator. The place for a discount or a seasonal rate, "
     "where somebody is already working out what a shipment will cost.",
     1200, 200, 1),
    ("tracking_sidebar", "Tracking page sidebar",
     "Beside the tracking result. High repeat traffic from customers who already "
     "ship with us, so it suits a next-shipment offer rather than an introduction.",
     320, 480, 2),
    ("blog_inline", "Blog in-article block",
     "Inserted between paragraphs of a blog post. Works when the offer matches "
     "what the article is about — a Dashain guide carrying a Dashain offer.",
     800, 300, 1),
    ("global_bar", "Site-wide top bar",
     "Thin bar above the header on every page. The most intrusive slot there is; "
     "reserve it for cut-off dates and disruptions rather than discounts.",
     1920, 60, 1),
]


# ── Campaigns and their creatives ────────────────────────────────────────────

#: Draft examples for the festivals and seasons this business genuinely sells
#: into. Dashain and Tihar are the two that move diaspora gift and cargo
#: volume; the student intake in August and September is the excess-baggage
#: season out of Kathmandu; Losar is a smaller but real audience.
#:
#: Country lists are the diaspora corridors Moonlight ships on, and they are
#: the only targeting there is. Nothing here is chosen from behaviour.
GULF = ["AE", "QA", "SA", "KW", "OM", "BH"]
WESTERN = ["GB", "US", "AU", "CA", "NZ", "IE"]
EAST_ASIA = ["JP", "KR", "MY"]

CAMPAIGNS = [
    {
        "code": "DASHAIN_GIFT_CARGO",
        "name": "Dashain gift and cargo season",
        "objective": "Diaspora gift parcels and consolidated cargo booked in the "
                     "weeks before Dashain, when the corridor is busiest and "
                     "bookings have to be early to arrive in time.",
        "occasion": "dashain",
        "landing_path": "/pricing",
        "utm_campaign": "dashain_gift_cargo",
        "creatives": [
            {
                "placement": "home_hero",
                "format": AdCreative.BANNER,
                "headline": "Send Dashain home, in time for Tika",
                "headline_ne": "दशैंमा घरमा उपहार पठाउनुहोस्",
                "body": "Gift parcels and consolidated cargo from the Gulf, the UK, "
                        "the US and Australia, delivered to the door anywhere in "
                        "Nepal. Customs paperwork handled, tracked the whole way.",
                "call_to_action": "Get a Dashain quote",
                "target_path": "/pricing",
                "countries": GULF + WESTERN + EAST_ASIA,
                "priority": 100,
            },
            {
                "placement": "global_bar",
                "format": AdCreative.STRIP,
                "headline": "Dashain cargo cut-off is approaching — book early to arrive before Tika",
                "body": "Sea freight closes first, then air cargo. Talk to us about "
                        "the last booking date for your corridor.",
                "call_to_action": "See cut-off dates",
                "target_path": "/contact",
                "countries": GULF + WESTERN + EAST_ASIA,
                "priority": 90,
            },
            {
                "placement": "blog_inline",
                "format": AdCreative.INLINE,
                "headline": "Shipping a Dashain parcel home?",
                "body": "Our rate calculator covers gift parcels from 1 kg upwards, "
                        "with door delivery across all 77 districts.",
                "call_to_action": "Check the rate",
                "target_path": "/pricing",
                "page_paths": ["/blog"],
                "priority": 40,
            },
        ],
    },
    {
        "code": "TIHAR_GIFT_PARCELS",
        "name": "Tihar gift parcels",
        "objective": "Smaller, later gift traffic than Dashain: parcels for Bhai "
                     "Tika that are booked after the Dashain rush has cleared.",
        "occasion": "tihar",
        "landing_path": "/pricing",
        "utm_campaign": "tihar_gift_parcels",
        "creatives": [
            {
                "placement": "home_offers",
                "format": AdCreative.CARD,
                "headline": "Tihar gifts, delivered to the door in Nepal",
                "headline_ne": "तिहारमा आफ्नो परिवारलाई उपहार पठाउनुहोस्",
                "body": "Sweets, clothes, cosmetics and electronics consolidated into "
                        "one parcel and delivered before Bhai Tika — Kathmandu valley "
                        "in two days, the districts in three to four.",
                "call_to_action": "Send a Tihar parcel",
                "target_path": "/pricing",
                "countries": GULF + WESTERN + EAST_ASIA,
                "priority": 80,
            },
            {
                "placement": "tracking_sidebar",
                "format": AdCreative.CARD,
                "headline": "Already shipped for Dashain? Tihar bookings are open",
                "body": "Repeat senders keep the same consignee address on file. One "
                        "click to send the next parcel to the same door.",
                "call_to_action": "Book the next one",
                "target_path": "/pricing",
                "priority": 50,
            },
        ],
    },
    {
        "code": "STUDENT_EXCESS_BAGGAGE",
        "name": "Student excess baggage season (Aug-Sep intake)",
        "objective": "Nepali students flying out for the autumn intake, who are over "
                     "the airline allowance and are quoted per kilo at the airport. "
                     "Peaks mid-August to late September.",
        "occasion": "student_season",
        "landing_path": "/pricing",
        "utm_campaign": "student_excess_baggage",
        "creatives": [
            {
                "placement": "home_strip",
                "format": AdCreative.STRIP,
                "headline": "Flying out to study? Send the rest as air cargo",
                "body": "Books, bedding, kitchen things and winter clothes, shipped "
                        "per kilogram and collected at your destination — instead of "
                        "paying excess baggage at the check-in desk.",
                "call_to_action": "Check baggage rates",
                "target_path": "/pricing",
                "countries": ["NP"],
                "priority": 70,
            },
            {
                "placement": "pricing_banner",
                "format": AdCreative.BANNER,
                "headline": "Student excess baggage, priced per kilogram",
                "body": "Door pickup in Kathmandu, Pokhara, Chitwan and Biratnagar. "
                        "Bring your admission letter and visa for the student rate.",
                "call_to_action": "Book a pickup",
                "target_path": "/contact",
                "countries": ["NP"],
                "page_paths": ["/pricing"],
                "priority": 60,
            },
        ],
    },
    {
        "code": "LOSAR_CARGO",
        "name": "Losar gift and cargo",
        "objective": "Losar traffic from Sherpa, Tamang, Gurung and Tibetan-Nepali "
                     "communities abroad. Smaller than Dashain, with more of it "
                     "going to the hill and mountain districts.",
        "occasion": "losar",
        "landing_path": "/pricing",
        "utm_campaign": "losar_cargo",
        "creatives": [
            {
                "placement": "home_offers",
                "format": AdCreative.CARD,
                "headline": "Losar parcels home, booked ahead",
                "body": "Consolidated air cargo into Kathmandu with onward delivery to "
                        "Solukhumbu, Mustang, Rasuwa and the hill districts. Book early "
                        "— road transfers upcountry add days in winter.",
                "call_to_action": "Plan a Losar shipment",
                "target_path": "/pricing",
                "countries": WESTERN + EAST_ASIA + ["IN"],
                "priority": 45,
            },
        ],
    },
]


# ── Notices ──────────────────────────────────────────────────────────────────

#: Examples of the other kind of message: things people need to know, not
#: things being sold. Seeded inactive and undated like everything else.
NOTICES = [
    {
        "code": "DASHAIN_OFFICE_HOURS",
        "title": "Dashain office hours",
        "body": "Our Kathmandu and Birgunj counters are closed for the public "
                "holidays. Online bookings and tracking continue as normal; "
                "collections booked over the holiday are made the next working day.",
        "style": SiteNotice.BAR,
        "tone": SiteNotice.INFO,
        "link_path": "/contact",
        "link_label": "Opening times",
        "dismiss_days": 7,
        "priority": 20,
    },
    {
        "code": "DASHAIN_CARGO_CUTOFF",
        "title": "Dashain cargo cut-off dates",
        "body": "Last booking dates for delivery before Tika differ by corridor and "
                "by service. Sea freight closes several weeks before air cargo.",
        "style": SiteNotice.BAR,
        "tone": SiteNotice.WARNING,
        "link_path": "/contact",
        "link_label": "See cut-off dates",
        "dismiss_days": 3,
        "priority": 30,
    },
]


#: Everything a native Nepali speaker should check before publication. The
#: English is ours to stand behind; the Nepali is not, until somebody who
#: speaks it has read it.
NEEDS_NATIVE_REVIEW = [
    "DASHAIN_GIFT_CARGO / home_hero: headline_ne 'दशैंमा घरमा उपहार पठाउनुहोस्'",
    "TIHAR_GIFT_PARCELS / home_offers: headline_ne 'तिहारमा आफ्नो परिवारलाई उपहार पठाउनुहोस्'",
    "All body_ne fields are empty and need writing, not translating - offer copy "
    "with rates and cut-off dates does not survive a literal translation.",
    "STUDENT_EXCESS_BAGGAGE has no Nepali at all, and is the one campaign whose "
    "audience is reading in Nepal. It needs Nepali copy the most.",
    "LOSAR_CARGO: Nepali may not even be the right language for this audience. "
    "Ask before writing it.",
]


def seed_advertising():
    """Create the standard placements and the draft example campaigns.

    Returns a count of what was created, so the command can say whether it
    did anything. Re-running it creates nothing and changes nothing.
    """
    created = {"placements": 0, "campaigns": 0, "creatives": 0, "notices": 0}

    placements = {}
    for code, name, description, width, height, max_creatives in PLACEMENTS:
        placement = AdPlacement.query.filter_by(code=code).first()
        if not placement:
            placement = AdPlacement(
                code=code, name=name, description=description,
                recommended_width=width, recommended_height=height,
                max_creatives=max_creatives, is_active=True,
            )
            db.session.add(placement)
            created["placements"] += 1
        placements[code] = placement
    db.session.flush()

    for spec in CAMPAIGNS:
        campaign = AdCampaign.query.filter_by(code=spec["code"]).first()
        if not campaign:
            campaign = AdCampaign(
                code=spec["code"],
                name=spec["name"],
                objective=spec["objective"],
                occasion=spec["occasion"],
                landing_path=spec["landing_path"],
                utm_campaign=spec["utm_campaign"],
                # Draft, and no dates. Activating is a deliberate act with a
                # real start and end, not something a deploy does.
                status=AdCampaign.DRAFT,
                starts_at=None,
                ends_at=None,
            )
            db.session.add(campaign)
            created["campaigns"] += 1
        db.session.flush()

        for index, item in enumerate(spec["creatives"]):
            placement = placements[item["placement"]]
            existing = AdCreative.query.filter_by(
                campaign_id=campaign.id, placement_id=placement.id,
                headline=item["headline"],
            ).first()
            if existing:
                continue
            db.session.add(AdCreative(
                campaign_id=campaign.id,
                placement_id=placement.id,
                format=item.get("format", AdCreative.BANNER),
                headline=item["headline"],
                headline_ne=item.get("headline_ne"),
                body=item.get("body"),
                body_ne=item.get("body_ne"),
                call_to_action=item.get("call_to_action"),
                target_path=item.get("target_path"),
                # No artwork is seeded: there is no upload mechanism in this
                # codebase, and a creative with an image and no alt text
                # cannot be activated anyway.
                image_path=None,
                image_alt=None,
                page_paths=item.get("page_paths", []),
                countries=item.get("countries", []),
                priority=item.get("priority", index),
                is_active=False,
            ))
            created["creatives"] += 1

    for spec in NOTICES:
        if SiteNotice.query.filter_by(code=spec["code"]).first():
            continue
        db.session.add(SiteNotice(
            code=spec["code"],
            title=spec["title"],
            body=spec["body"],
            style=spec["style"],
            tone=spec["tone"],
            link_path=spec["link_path"],
            link_label=spec["link_label"],
            dismiss_days=spec["dismiss_days"],
            priority=spec["priority"],
            page_paths=[],
            starts_at=None,
            ends_at=None,
            is_active=False,
        ))
        created["notices"] += 1

    db.session.commit()
    return created
