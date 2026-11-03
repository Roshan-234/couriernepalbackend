"""The frontend's copy of Nepal's districts must match this one.

`src/lib/seo/districts.ts` in the frontend duplicates `app/data/nepal_geo.py`
because `generateStaticParams` runs during `next build`, which happens in CI
where this API is not reachable. Duplication is the right call there, but it
needs a guard: a district renamed or rezoned here, and not there, silently
produces a landing page that contradicts the quote the same visitor is given.

The test is skipped when the frontend checkout is not beside this one, so the
backend stays installable on its own.
"""
import json
import re
from pathlib import Path

import pytest

from app.data.nepal_geo import DISTRICTS, PROVINCES, ZONE_CODES

FRONTEND = Path(__file__).resolve().parents[2] / "couriernepalfrontend"
MIRROR = FRONTEND / "src" / "lib" / "seo" / "districts.ts"

pytestmark = pytest.mark.skipif(
    not MIRROR.exists(),
    reason="frontend checkout not present beside the backend",
)


def _parse_objects(source, start_marker):
    """Pull the `{ ... }` literals out of one exported array."""
    start = source.index(start_marker)
    # One array ends `];` and the other `] as const;`. Take whichever
    # terminator comes first, or the parse runs on into the next array.
    candidates = [source.find(token, start) for token in ("];", "] as const;")]
    end = min(position for position in candidates if position != -1)
    body = source[start:end]
    rows = []
    for match in re.finditer(r"\{[^{}]*\}", body):
        # The generator emits JSON-quoted keys' values but bare keys, so
        # quote the keys before handing it to the JSON parser.
        text = re.sub(r"(\w+):", r'"\1":', match.group(0))
        rows.append(json.loads(text))
    return rows


@pytest.fixture(scope="module")
def mirror():
    return MIRROR.read_text(encoding="utf-8")


def test_district_names_and_slugs_match(mirror):
    theirs = _parse_objects(mirror, "export const DISTRICTS:")
    assert len(theirs) == len(DISTRICTS) == 77

    ours = {d["slug"]: d for d in DISTRICTS}
    for row in theirs:
        assert row["slug"] in ours, f"frontend has an unknown district: {row['slug']}"
        mine = ours[row["slug"]]
        assert row["name"] == mine["name"]
        assert row["headquarters"] == mine["headquarters"]
        assert row["province"] == mine["province"]
        assert row["provinceCode"] == mine["province_code"]


def test_delivery_zones_match(mirror):
    """A mismatch here would quote one price and advertise another."""
    theirs = {row["slug"]: row["zone"] for row in _parse_objects(mirror, "export const DISTRICTS:")}
    for district in DISTRICTS:
        assert theirs[district["slug"]] == district["zone"], (
            f"{district['name']} is '{district['zone']}' here but "
            f"'{theirs[district['slug']]}' in the frontend"
        )


def test_zone_vocabulary_matches(mirror):
    declared = set(re.findall(r"^\s{2}(\w+): \{ label:", mirror, re.MULTILINE))
    assert declared == set(ZONE_CODES)


def test_provinces_match(mirror):
    theirs = _parse_objects(mirror, "export const PROVINCES")
    assert len(theirs) == len(PROVINCES) == 7
    for row in theirs:
        assert row["name"] in PROVINCES, f"unknown province: {row['name']}"
        meta = PROVINCES[row["name"]]
        assert row["code"] == meta["code"]
        assert row["capital"] == meta["capital"]
        assert row["districtCount"] == len(meta["districts"])
