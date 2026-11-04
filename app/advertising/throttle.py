"""Counter integrity for the public impression and click endpoints.

``POST /api/ads/impression`` is an unauthenticated write to a number that a
campaign is later judged on. Left open, one caller with a loop can make a
dead campaign look like the best one the company ever ran, and the report
that decides next year's festival spend is then fiction.

Two different things are being defended, which is why there are two
mechanisms:

``flask_limiter`` guards *the endpoint*
    It caps requests per address or account across the deployment and is the
    right place for a blunt budget. It is also configurable per environment
    and is switched off entirely in tests, which is exactly why it cannot be
    the only defence.

This module guards *the number*
    A per-visitor window and a per-creative dedup, applied in the handler
    before anything is written. An impression counts at most once per
    visitor per creative per ``IMPRESSION_DEDUP_SECONDS``, so the figure
    means "how many visitors saw this", not "how many times it rendered".
    Clicks are rate limited but not deduplicated: a visitor who genuinely
    clicks twice has clicked twice, and silently dropping the second one
    would understate the only number that measures interest.

What it does not do, honestly stated: state is per process and in memory, so
behind several workers the effective allowance multiplies by the worker
count, and a restart forgets everything. That is enough to stop a casual
script and is not enough to stop a determined distributed effort. It holds
no raw address — the bucket is the consent cookie's subject key where there
is one, otherwise a salted hash — and it is never consulted when deciding
what to serve, so it is counter-fraud control rather than anything that
could be called profiling.
"""

import threading
import time

#: Events of any kind one visitor may send in a window before being refused.
#: Generous on purpose: a long page with several placements legitimately
#: reports a handful of impressions at once, and a visitor reading twenty
#: pages in a minute is a person, not an attack.
WINDOW_SECONDS = 60
MAX_EVENTS_PER_WINDOW = 60

#: How long the same visitor's repeat view of the same creative is ignored.
IMPRESSION_DEDUP_SECONDS = 1800

#: Ceiling on tracked keys, so a flood of distinct buckets cannot grow the
#: process without bound. Pruning is opportunistic rather than scheduled;
#: this is a cache, and losing it only means something counts twice.
MAX_TRACKED_KEYS = 20000

OK = "ok"
RATE_LIMITED = "rate_limited"
DUPLICATE = "duplicate"

_LOCK = threading.Lock()
#: bucket -> (window started at, events so far)
_windows = {}
#: (bucket, kind, object id) -> monotonic time the dedup expires
_seen = {}


def _prune(now):
    """Drop everything that has expired. Called under the lock."""
    for key, expiry in list(_seen.items()):
        if expiry <= now:
            _seen.pop(key, None)
    for key, (started, _count) in list(_windows.items()):
        if now - started >= WINDOW_SECONDS:
            _windows.pop(key, None)


def allow(bucket, kind, object_id, dedup_seconds=0):
    """Decide whether an advert event may be counted.

    Returns ``OK``, ``RATE_LIMITED`` (the caller should be refused) or
    ``DUPLICATE`` (the caller is answered normally but nothing is counted —
    a duplicate is not misbehaviour, it is the same person on a second page
    view, and telling them apart would need the browser state this module
    deliberately does not have).

    ``time.monotonic`` rather than the wall clock, so a clock adjustment
    cannot reopen a window or extend a dedup.
    """
    now = time.monotonic()
    with _LOCK:
        if len(_seen) + len(_windows) > MAX_TRACKED_KEYS:
            _prune(now)

        started, count = _windows.get(bucket, (now, 0))
        if now - started >= WINDOW_SECONDS:
            started, count = now, 0
        if count >= MAX_EVENTS_PER_WINDOW:
            # Hold the window open at its ceiling rather than extending it,
            # so a caller who keeps hammering is refused for the remainder
            # of the window instead of resetting their own clock.
            _windows[bucket] = (started, count)
            return RATE_LIMITED
        _windows[bucket] = (started, count + 1)

        if dedup_seconds:
            key = (bucket, kind, object_id)
            expiry = _seen.get(key)
            if expiry is not None and expiry > now:
                return DUPLICATE
            _seen[key] = now + dedup_seconds

    return OK


def reset():
    """Forget every window and dedup.

    Exists for the tests, which need a clean slate between cases because the
    state is process-wide. Nothing in the application calls it.
    """
    with _LOCK:
        _windows.clear()
        _seen.clear()
