"""The public privacy, cookie and data-rights pages.

These are kept apart from `seed.py` for one reason: the rest of the starter
content is marketing copy that an administrator is free to rewrite, while
this text is a description of what the software actually does. When the code
changes — a new cookie, a new retention period, a new processor — this file
has to change with it, and the page has to be re-published. Copy that drifts
away from the system is worse than no copy, because it reads as a promise.

The `/privacy` page that shipped before this module said analytics ran
through Google Analytics and that the visitor's preference lived in their
browser's local storage. Neither is true any more: analytics is first-party
and the consent decision is a row on our own server. `seed_privacy_pages()`
below therefore *replaces* that page when it is still the old default text,
rather than skipping it the way ordinary content seeding does.

Everything marked [TO CONFIRM] is a fact about the company, not about the
code, and cannot be answered from here.
"""

from app.extensions import db
from app.models.consent import (
    CATEGORY_DESCRIPTIONS,
    CONSENT_LIFETIME_DAYS,
    CURRENT_POLICY_VERSION,
)
from app.models.privacy import DataRequest
from app.models.site_page import SitePage

#: Identifies the page text that this module supersedes. If the row still
#: starts with this, nobody has edited it and it is safe to replace.
_STALE_PRIVACY_MARKER = "We use the information you provide to manage your account"


PRIVACY = {
    "path": "/privacy",
    "title": "Privacy policy",
    "eyebrow": "HOW WE HANDLE YOUR INFORMATION",
    "heading": "Privacy policy",
    "intro": (
        "Moonlight Freight Pvt. Ltd. carries documents, parcels and cargo between "
        "Nepal and the rest of the world. Doing that means handling information "
        "about senders and recipients. This page sets out what we collect, why, "
        "how long we keep it, and what you can ask us to do about it."
    ),
    "sections": [
        ("Who is responsible",
         "Moonlight Freight Pvt. Ltd., Nayabazar, Kathmandu, Nepal, is the data "
         "controller for the information described here. Questions about this "
         "policy, or a request about your own data, can be sent to "
         "moonlightfreight@gmail.com or raised through the data request form. "
         "[TO CONFIRM: company registration number, registered address as filed, "
         "and whether a named privacy contact should be published here.]"),

        ("What we collect to carry a shipment",
         "To move a parcel we need the sender's and recipient's name, address, "
         "phone number and, where given, email address; a description of the "
         "contents and declared value for customs; and the weight and dimensions "
         "of the package. For international shipments, customs and airline "
         "security rules require some of this to be passed to border authorities "
         "and to the carrier handling each leg. We cannot ship without it."),

        ("What we collect because you asked us to",
         "An account holds the name, email address and phone number you entered, "
         "your saved addresses and your shipment history. A quote request holds "
         "the route and weight you typed. A message to our team holds whatever "
         "you wrote in it. A newsletter subscription holds your email address and "
         "the record of you confirming it."),

        ("Our security log",
         "Separately from analytics, we keep an administrative log of actions "
         "taken through our systems — signing in, creating or changing a "
         "shipment, an administrator opening a record. Unlike the analytics "
         "measurements, this log does record the IP address and browser the "
         "action came from, because its purpose is to let us investigate a "
         "compromised account or an unauthorised change. We keep it for one "
         "year. Ordinary browsing does not appear in it: the analytics beacon, "
         "the adverts on a page and checking your cookie settings are all "
         "excluded, so it records deliberate actions rather than visits. "
         "[TO CONFIRM: your own security policy may require a different "
         "retention period.]"),

        ("What we collect automatically",
         "Our web server keeps short-lived request logs. Our own analytics counts "
         "page views, so we can see which pages are useful. Those measurements are "
         "taken on our own servers and are not shared with an advertising network. "
         "We do not store your IP address in them: a salted, one-way hash is kept "
         "instead, which lets us filter automated traffic without recording who "
         "you are. Nothing is linked to you as an individual unless you have "
         "allowed analytics cookies, and even then the identifier is a random "
         "value that means nothing outside this site."),

        ("Why we are allowed to do this",
         "Carrying a shipment you booked, and keeping your account, is the "
         "performance of a contract. Customs declarations and tax records are a "
         "legal obligation. Keeping the site secure, preventing fraud and "
         "improving our service are legitimate interests, balanced against your "
         "rights. Marketing emails and optional cookies rest on your consent, "
         "which you can withdraw at any time without affecting anything that "
         "happened before you withdrew it."),

        ("Who else sees it",
         "The airline, freight forwarder or partner carrier handling a leg of "
         "your shipment; customs and border authorities in the origin, transit "
         "and destination countries; our payment provider, when you pay online; "
         "and the providers who host our systems and send our email. We do not "
         "sell personal information, and we do not share it for anyone else's "
         "advertising. [TO CONFIRM: the current list of carriers, payment "
         "providers and hosting providers should be named here, and a written "
         "processing agreement should exist with each one.]"),

        ("Sending information out of Nepal",
         "An international shipment moves data across borders by its nature: a "
         "parcel to Germany means a German address reaching a German carrier and "
         "German customs. Where we send information about someone in the European "
         "Economic Area or the United Kingdom, we rely on the transfer being "
         "necessary to perform the contract you asked for. [TO CONFIRM: Nepal has "
         "no EU adequacy decision. If the company offers services to people in "
         "the EEA or UK on an ongoing basis, take advice on GDPR Article 27 — "
         "whether a representative in the EU or UK must be appointed — and on "
         "standard contractual clauses with processors. This is a question for a "
         "lawyer, not for this website.]"),

        ("How long we keep it",
         "Shipment and invoice records are kept for as long as tax and carriage "
         "law requires, which sets the floor, and are then removed. Website "
         "analytics are kept for 90 days. Records of consent are kept beyond the "
         "life of the consent itself, so that we can still show what you agreed "
         "to and when. If you unsubscribe from our emails we keep a minimal "
         "record of that fact, because deleting it entirely would mean a future "
         "import quietly adding you back. [TO CONFIRM with an accountant: the "
         "retention period Nepali tax and company law requires for invoices and "
         "consignment records.]"),

        ("Your rights",
         "You can ask for a copy of the information we hold about you, ask us to "
         "correct it, ask us to delete it, ask us to restrict or stop a "
         "particular use, or ask for it in a portable format. You can object to "
         "processing based on legitimate interests. If you have an account you "
         "can download your own data immediately from your account page. "
         f"Otherwise use the data request form. We answer within {DataRequest.RESPONSE_DAYS} "
         "days, and will tell you if a complex request needs longer. We will ask "
         "you to prove who you are first — otherwise the process becomes a way "
         "for somebody else to obtain your records."),

        ("What we cannot delete",
         "Erasure is not absolute. Where a record has to be kept for tax, "
         "customs or carriage liability we will remove or replace the parts that "
         "identify you and keep the rest. We will always tell you what was "
         "deleted and what was retained, and why."),

        ("Complaints",
         "If you are not satisfied with how we have handled your information, "
         "tell us first and we will try to put it right. If you are in the "
         "European Economic Area or the United Kingdom you also have the right "
         "to complain to your national data protection authority."),

        ("Changes to this policy",
         f"The current version of our cookie and consent wording is "
         f"{CURRENT_POLICY_VERSION}. When the wording or the purposes change we "
         "publish a new version and ask again rather than assuming your previous "
         "answer still applies."),
    ],
}


COOKIES = {
    "path": "/cookies",
    "title": "Cookies and similar technologies",
    "eyebrow": "WHAT WE STORE IN YOUR BROWSER",
    "heading": "Cookies",
    "intro": (
        "A cookie is a small value a website asks your browser to keep. We use "
        "very few, we load nothing from an advertising network, and nothing "
        "optional is stored until you have said yes."
    ),
    "sections": [
        ("Nothing optional runs before you choose",
         "Until you answer the cookie banner, the only things stored are the ones "
         "needed to deliver the page you asked for. We do not set an analytics "
         "identifier, show a personalised offer, or record who you are. If you "
         "close the banner without choosing, that is treated as a refusal."),

        ("Strictly necessary",
         CATEGORY_DESCRIPTIONS["necessary"] + " These are: the session cookie "
         "that keeps you signed in, the CSRF token that stops another site "
         "submitting forms as you, and mf_vid, which is the random identifier "
         "that lets us remember the choice you made here. Without that last one "
         "we would have to ask you on every single page."),

        ("Analytics",
         CATEGORY_DESCRIPTIONS["analytics"] + " If you allow this, mf_vid is used "
         "to count you as one returning visitor rather than several. If you "
         "refuse, we still count the page view, but with no identifier attached "
         "at all, so it cannot be joined up into a journey."),

        ("Marketing and advertising",
         CATEGORY_DESCRIPTIONS["marketing"] + " In practice this means measuring "
         "whether an offer on our own site was seen and clicked. We do not run "
         "third-party advertising tags, we do not build an interest profile, and "
         "we do not show you an offer because of what you looked at before. "
         "Offers are chosen by which page you are on and which country you are "
         "reading from."),

        ("Preferences",
         CATEGORY_DESCRIPTIONS["preferences"] + " For example, remembering that "
         "you dismissed an announcement so it does not reappear on every page."),

        ("Changing your mind",
         f"Your choice is kept for {CONSENT_LIFETIME_DAYS} days and then we ask "
         "again. You can change or withdraw it at any time from the cookie "
         "settings link in the footer; withdrawing takes exactly as few clicks "
         "as agreeing did. We keep a dated record of each choice so that we can "
         "show what was agreed, which is why the record itself is necessary."),

        ("No Google Analytics",
         "Earlier versions of this site loaded Google Analytics. It no longer "
         "does. Visit counting now happens on our own servers, which keeps the "
         "data in one place and under one retention rule."),
    ],
}


DATA_REQUESTS = {
    "path": "/data-requests",
    "title": "Request your data",
    "eyebrow": "ACCESS, CORRECTION AND DELETION",
    "heading": "Make a data request",
    "intro": (
        "Ask for a copy of what we hold about you, ask us to correct it, or ask "
        "us to delete it. You do not need an account to make a request."
    ),
    "sections": [
        ("What you can ask for",
         "A copy of your data; a correction to something that is wrong; deletion; "
         "a restriction on a particular use while something is being sorted out; "
         "your data in a portable, machine-readable file; or an objection to a "
         "use you disagree with."),

        ("How long it takes",
         f"We answer within {DataRequest.RESPONSE_DAYS} days of being able to "
         "confirm who you are. A complicated request can take up to two months "
         "longer, and if that happens we will tell you within the first month "
         "and explain why."),

        ("Proving who you are",
         "We will ask you to confirm your identity before we hand anything over "
         "or delete anything. This is not an obstacle we have invented: without "
         "it, anyone who knows your email address could request your shipping "
         "history or have your account erased."),

        ("If you have an account",
         "Sign in and download your data immediately from your account page — it "
         "is faster than a request and needs no verification step, because you "
         "have already proved who you are by signing in."),

        ("What happens to a deletion request",
         "We delete what we can and anonymise what we must keep for tax, customs "
         "or liability reasons, and we tell you which was which. We also keep a "
         "record that the request was made and answered, because being able to "
         "show that is part of handling it properly."),
    ],
}

PAGES = (PRIVACY, COOKIES, DATA_REQUESTS)


def seed_privacy_pages(force=False):
    """Publish the three pages from code. Returns what it did, per path.

    **These three pages are owned by this file, not by the page editor.**
    That is the opposite of how the rest of the site content works, and it is
    deliberate. Everything else under `/api/content` is copy, where an
    administrator's wording should win. These three are a description of what
    the software does, and the tests in `tests/test_privacy_pages.py` check
    the published text against the constants that make it true — the response
    deadline, the consent lifetime, the cookie name. A policy that can drift
    away from the system is the failure those tests exist to prevent, so the
    seed refreshes them on every boot rather than preserving an edit.

    An earlier version of this function tried to be polite about it: replace
    the page only while it still held the pre-consent-system default, and
    otherwise assume a human had improved it. That does not survive its own
    second revision — once this module has published once, its *next* change
    looks exactly like an administrator's edit and is skipped, leaving the
    policy frozen at whatever was true the first time. Hence: code wins, and
    says so out loud when it overwrites something it did not write.

    `force` is kept only so a caller can refresh the pages without the log
    line; it no longer changes what gets written.
    """
    import logging

    results = {}
    for page in PAGES:
        existing = SitePage.query.filter_by(path=page["path"]).first()
        sections = [{"heading": h, "body": b} for h, b in page["sections"]]

        if existing is None:
            db.session.add(SitePage(
                path=page["path"], title=page["title"],
                description=page["intro"][:320], eyebrow=page["eyebrow"],
                heading=page["heading"], intro=page["intro"], sections=sections,
                published=True,
            ))
            results[page["path"]] = "created"
            continue

        if (existing.intro == page["intro"] and existing.sections == sections
                and existing.title == page["title"]
                and existing.heading == page["heading"]
                and existing.eyebrow == page["eyebrow"]):
            results[page["path"]] = "already current"
            continue

        # Distinguish the two reasons the text can differ, because one of
        # them means somebody's work is about to be discarded.
        was_stale_default = (page["path"] == "/privacy"
                             and (existing.intro or "").startswith(_STALE_PRIVACY_MARKER))
        if was_stale_default:
            outcome = "replaced (described a system we no longer run)"
        else:
            outcome = "refreshed from code"
            if not force:
                logging.getLogger(__name__).warning(
                    "%s differed from the policy text in app/content/"
                    "privacy_pages.py and has been overwritten. These pages are "
                    "generated from code so they cannot drift from the system "
                    "they describe; edit that file, not the page editor.",
                    page["path"],
                )

        existing.title = page["title"]
        existing.description = page["intro"][:320]
        existing.eyebrow = page["eyebrow"]
        existing.heading = page["heading"]
        existing.intro = page["intro"]
        existing.sections = sections
        existing.published = True
        existing.version = (existing.version or 1) + 1
        results[page["path"]] = outcome

    db.session.commit()
    return results
