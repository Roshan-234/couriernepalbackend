"""Data-subject export bundles on disk: who can read them, and for how long.

An export is the most sensitive object this system produces — one person's
complete record, in plain JSON, written so it can be handed to them. Two
things follow, and neither was true when the module was first written: the
file must not be readable by other accounts on the host, and it must not
still be sitting there months later. A folder of these is a worse exposure
than the database rows they came from, because it is flat, complete and
outside every access control the application enforces.

The permission assertions only mean something on POSIX, where the site is
served. Windows, where development happens, reports a mode that does not
reflect its real ACLs, so those assertions are skipped there rather than
asserted falsely.
"""

import json
import os
import time
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.privacy import DataRequest
from app.privacy_requests.routes import (
    EXPORT_RETENTION_DAYS,
    _export_dir,
    ensure_private_dir,
)

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="file modes are not meaningful on Windows; the host that serves "
           "this site is Linux and that is where the assertion matters",
)


@pytest.fixture
def export_dir(app, tmp_path):
    """Point the module at a throwaway directory, never the repo's storage/."""
    previous = app.config.get("PRIVACY_EXPORT_DIR")
    app.config["PRIVACY_EXPORT_DIR"] = str(tmp_path / "exports")
    yield str(tmp_path / "exports")
    if previous is None:
        app.config.pop("PRIVACY_EXPORT_DIR", None)
    else:
        app.config["PRIVACY_EXPORT_DIR"] = previous


def _write_bundle(directory, name, age_days=0):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"profile": {"email": "someone@example.com"}}, handle)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return os.path.abspath(path)


# ── Permissions ──────────────────────────────────────────────────────────────

def test_the_export_directory_is_created_restricted(app, export_dir):
    with app.app_context():
        ensure_private_dir(export_dir)
    assert os.path.isdir(export_dir)


@POSIX_ONLY
def test_the_directory_mode_excludes_other_accounts(app, export_dir):
    with app.app_context():
        ensure_private_dir(export_dir)
    assert os.stat(export_dir).st_mode & 0o077 == 0, (
        "the export directory is readable outside the service account")


@POSIX_ONLY
def test_an_existing_loose_directory_is_tightened(app, export_dir):
    """`makedirs` ignores `mode` when the path already exists.

    The first version of this hardening only protected fresh installs; an
    existing deployment kept whatever the umask gave it. Hence the explicit
    chmod, and hence this test.
    """
    os.makedirs(export_dir, exist_ok=True)
    os.chmod(export_dir, 0o755)
    with app.app_context():
        ensure_private_dir(export_dir)
    assert os.stat(export_dir).st_mode & 0o077 == 0


# ── Pruning ──────────────────────────────────────────────────────────────────

def test_a_dry_run_deletes_nothing(app, export_dir):
    old = _write_bundle(export_dir, "DSR-OLD-1.json", age_days=EXPORT_RETENTION_DAYS + 5)
    result = app.test_cli_runner().invoke(args=["privacy-exports-prune"])
    assert result.exit_code == 0, result.output
    assert "Would delete" in result.output
    assert "Dry run" in result.output
    assert os.path.exists(old), "a dry run removed a file"


def test_apply_removes_only_what_is_past_the_period(app, export_dir):
    old = _write_bundle(export_dir, "DSR-OLD-2.json", age_days=EXPORT_RETENTION_DAYS + 1)
    fresh = _write_bundle(export_dir, "DSR-NEW-2.json")

    result = app.test_cli_runner().invoke(args=["privacy-exports-prune", "--apply"])
    assert result.exit_code == 0, result.output
    assert not os.path.exists(old)
    assert os.path.exists(fresh), "a bundle inside the retention period was deleted"


def test_pruning_clears_the_path_on_the_request_record(app, export_dir):
    """The queue must never point an administrator at a file that is gone."""
    path = _write_bundle(export_dir, "DSR-OLD-3.json", age_days=EXPORT_RETENTION_DAYS + 1)
    with app.app_context():
        record = DataRequest(
            reference="DSR-TEST-PRUNE", kind=DataRequest.ACCESS,
            email="prune@example.com", export_path=path,
        )
        record.start_clock()
        db.session.add(record)
        db.session.commit()
        record_id = record.id

    app.test_cli_runner().invoke(args=["privacy-exports-prune", "--apply"])

    with app.app_context():
        assert db.session.get(DataRequest, record_id).export_path is None


def test_nothing_happens_when_there_is_no_directory(app, export_dir):
    result = app.test_cli_runner().invoke(args=["privacy-exports-prune", "--apply"])
    assert result.exit_code == 0
    assert "nothing to do" in result.output.lower()


def test_a_negative_period_is_refused(app, export_dir):
    result = app.test_cli_runner().invoke(
        args=["privacy-exports-prune", "--days", "-1", "--apply"])
    assert result.exit_code != 0


def test_non_bundle_files_are_left_alone(app, export_dir):
    """Only this command's own output is its to delete."""
    os.makedirs(export_dir, exist_ok=True)
    stray = os.path.join(export_dir, "README.txt")
    with open(stray, "w", encoding="utf-8") as handle:
        handle.write("kept by the operations team")
    old = time.time() - 400 * 86400
    os.utime(stray, (old, old))

    app.test_cli_runner().invoke(args=["privacy-exports-prune", "--apply"])
    assert os.path.exists(stray)


def test_the_default_directory_is_inside_ignored_storage(app):
    """A bundle must never be committable.

    `storage/` is in .gitignore; this pins the default path to it so a
    future change of location has to think about that again.
    """
    with app.app_context():
        app.config.pop("PRIVACY_EXPORT_DIR", None)
        assert "storage" in _export_dir().replace("\\", "/").split("/")
