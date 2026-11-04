"""Data subject requests (DSAR) and the retention schedule.

Named ``privacy_requests`` rather than ``privacy`` on purpose: ``app.models.
privacy`` already exists, and a top-level ``app/privacy`` package would shadow
it for anything doing ``from app.privacy import ...`` during model import.
"""
