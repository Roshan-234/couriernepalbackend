"""Shipment lifecycle states and the transitions allowed between them.

The original code treated status as a free-form string, so any value could be
written over any other and a delivered parcel could silently go back to
pending. Courier operations need the opposite: a small vocabulary, and a
transition table that an operator, a rider and a carrier webhook all share.

Statuses are grouped so the dashboard and the customer timeline can describe
progress without hard-coding the list in several places.
"""

# Booking
DRAFT = "draft"
PENDING = "pending"

# Collection
PICKUP_ASSIGNED = "pickup_assigned"
PICKED_UP = "picked_up"

# Line haul
AT_ORIGIN_BRANCH = "at_origin_branch"
IN_TRANSIT = "in_transit"
AT_DESTINATION_BRANCH = "at_destination_branch"
CUSTOMS_HOLD = "customs_hold"

# Last mile
OUT_FOR_DELIVERY = "out_for_delivery"
DELIVERY_ATTEMPTED = "delivery_attempted"
DELIVERED = "delivered"

# Return to origin
RTO_INITIATED = "rto_initiated"
RTO_IN_TRANSIT = "rto_in_transit"
RETURNED = "returned"

# Exceptions and closure
ON_HOLD = "on_hold"
LOST = "lost"
DAMAGED = "damaged"
CANCELLED = "cancelled"

#: Terminal states. A shipment in one of these never moves again.
TERMINAL = frozenset({DELIVERED, RETURNED, CANCELLED, LOST})

#: Legacy vocabulary kept so historic rows and existing clients stay valid.
LEGACY_STATUSES = (
    PENDING, PICKED_UP, IN_TRANSIT, OUT_FOR_DELIVERY,
    DELIVERED, CANCELLED, RETURNED,
)

GROUPS = {
    "booking": (DRAFT, PENDING),
    "collection": (PICKUP_ASSIGNED, PICKED_UP),
    "line_haul": (AT_ORIGIN_BRANCH, IN_TRANSIT, AT_DESTINATION_BRANCH, CUSTOMS_HOLD),
    "last_mile": (OUT_FOR_DELIVERY, DELIVERY_ATTEMPTED, DELIVERED),
    "return": (RTO_INITIATED, RTO_IN_TRANSIT, RETURNED),
    "exception": (ON_HOLD, LOST, DAMAGED, CANCELLED),
}

LABELS = {
    DRAFT: "Draft",
    PENDING: "Booked, awaiting pickup",
    PICKUP_ASSIGNED: "Pickup assigned",
    PICKED_UP: "Picked up",
    AT_ORIGIN_BRANCH: "At origin branch",
    IN_TRANSIT: "In transit",
    AT_DESTINATION_BRANCH: "At destination branch",
    CUSTOMS_HOLD: "Held at customs",
    OUT_FOR_DELIVERY: "Out for delivery",
    DELIVERY_ATTEMPTED: "Delivery attempted",
    DELIVERED: "Delivered",
    RTO_INITIATED: "Return to sender started",
    RTO_IN_TRANSIT: "Returning to sender",
    RETURNED: "Returned to sender",
    ON_HOLD: "On hold",
    LOST: "Lost in transit",
    DAMAGED: "Damaged",
    CANCELLED: "Cancelled",
}

#: Allowed forward moves. Anything not listed here is rejected.
TRANSITIONS = {
    DRAFT: {PENDING, CANCELLED},
    PENDING: {PICKUP_ASSIGNED, PICKED_UP, ON_HOLD, CANCELLED},
    PICKUP_ASSIGNED: {PICKED_UP, PENDING, ON_HOLD, CANCELLED},
    PICKED_UP: {AT_ORIGIN_BRANCH, IN_TRANSIT, ON_HOLD, DAMAGED, LOST},
    AT_ORIGIN_BRANCH: {IN_TRANSIT, ON_HOLD, DAMAGED, LOST},
    IN_TRANSIT: {AT_DESTINATION_BRANCH, CUSTOMS_HOLD, OUT_FOR_DELIVERY,
                 ON_HOLD, DAMAGED, LOST},
    CUSTOMS_HOLD: {IN_TRANSIT, AT_DESTINATION_BRANCH, RTO_INITIATED, ON_HOLD},
    AT_DESTINATION_BRANCH: {OUT_FOR_DELIVERY, ON_HOLD, RTO_INITIATED, DAMAGED, LOST},
    OUT_FOR_DELIVERY: {DELIVERED, DELIVERY_ATTEMPTED, ON_HOLD, DAMAGED, LOST},
    DELIVERY_ATTEMPTED: {OUT_FOR_DELIVERY, AT_DESTINATION_BRANCH,
                         RTO_INITIATED, ON_HOLD},
    RTO_INITIATED: {RTO_IN_TRANSIT, ON_HOLD},
    RTO_IN_TRANSIT: {RETURNED, ON_HOLD, LOST, DAMAGED},
    ON_HOLD: {PENDING, PICKED_UP, AT_ORIGIN_BRANCH, IN_TRANSIT,
              AT_DESTINATION_BRANCH, OUT_FOR_DELIVERY, RTO_INITIATED, CANCELLED},
    DAMAGED: {RTO_INITIATED, DELIVERED, LOST},
    # Terminal
    DELIVERED: set(),
    RETURNED: set(),
    CANCELLED: set(),
    LOST: set(),
}

STATUSES = tuple(TRANSITIONS)

#: Progress along the normal delivery line. Operations routinely skip a scan —
#: a parcel booked in the morning can be on a truck by noon without anyone
#: recording "picked up" — so a jump forward along this line is allowed. What
#: the machine refuses is moving backwards, or leaving a terminal state.
#: Statuses absent from this map (holds, returns, write-offs) are reachable
#: only through the explicit table above.
PROGRESS = {
    DRAFT: 0,
    PENDING: 1,
    PICKUP_ASSIGNED: 2,
    PICKED_UP: 3,
    AT_ORIGIN_BRANCH: 4,
    IN_TRANSIT: 5,
    AT_DESTINATION_BRANCH: 6,
    OUT_FOR_DELIVERY: 7,
    DELIVERY_ATTEMPTED: 7,
    DELIVERED: 8,
}

#: Statuses an unprivileged delivery agent may set. Write-offs and
#: cancellations stay with operations staff.
AGENT_STATUSES = frozenset({
    PICKED_UP, AT_ORIGIN_BRANCH, IN_TRANSIT, AT_DESTINATION_BRANCH,
    OUT_FOR_DELIVERY, DELIVERY_ATTEMPTED, DELIVERED,
})


def is_valid(status):
    return status in TRANSITIONS


def is_terminal(status):
    return status in TERMINAL


def label(status):
    return LABELS.get(status, str(status).replace("_", " ").capitalize())


def group_of(status):
    for name, members in GROUPS.items():
        if status in members:
            return name
    return "exception"


def can_transition(current, target):
    """True when target is reachable from current.

    Rows created before the state machine existed may hold a status that is no
    longer in the table. Rather than freezing those shipments, an unknown
    current status accepts any valid target so operations can correct it.
    """
    if not is_valid(target):
        return False
    if current == target:
        return True
    if not is_valid(current):
        return True
    if is_terminal(current):
        return False
    if target in TRANSITIONS[current]:
        return True
    # A skipped scan is normal; going backwards is not.
    here, there = PROGRESS.get(current), PROGRESS.get(target)
    return here is not None and there is not None and there > here


def has_reached(current, milestone):
    """True when ``current`` is at or past ``milestone`` on the delivery line.

    A skipped scan is fine when operations are correcting a status, but a
    rider cannot deliver a parcel nobody collected. Delivery actions use this
    as a precondition rather than relying on the transition table alone.
    Statuses off the main line (holds, returns) report False.
    """
    here, there = PROGRESS.get(current), PROGRESS.get(milestone)
    return here is not None and there is not None and here >= there


def next_statuses(current):
    """Targets an operator may choose from, sorted for stable display."""
    if not is_valid(current):
        return sorted(STATUSES)
    return sorted(target for target in STATUSES if target != current
                  and can_transition(current, target))
