"""First-party website analytics.

The HTTP surface is in :mod:`app.analytics.routes`; the write path is in
:mod:`app.analytics.collect` so that a backend module wanting to log an
event does not have to import a Flask-RESTX namespace to do it. The CLI
jobs that roll events up and purge them live in
:mod:`app.analytics.commands`.

Nothing in this package talks to anything outside this process. That is
the whole reason it exists: see the docstring on ``app/models/analytics.py``.
"""
