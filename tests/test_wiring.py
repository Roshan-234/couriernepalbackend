"""The application must actually mount the modules it ships.

Every other test file builds the app and calls a URL, which looks like
proof that the URL exists — but a test file can register its own namespace
at import time, and then it is testing a route the real application does
not serve. That is not hypothetical: the consent, analytics, advertising,
marketing and data-rights modules were each written with a mounting shim in
their test file while `app/swagger_setup.py` was being edited centrally, and
for a while all of them passed against routes no browser could reach.

So this file asserts the wiring itself, against a plain `create_app()` with
nothing else imported: the namespaces are mounted at the paths the frontend
and the outbound email already depend on, and the CLI commands exist. It is
the cheapest test here and the only one that catches a module that was
built, tested and then never plugged in.
"""

import pytest

#: Path -> a route that must exist under it. The paths are part of the
#: contract: `/api/marketing` is baked into the confirm and unsubscribe
#: links that go out in mail, so changing it silently breaks every link in
#: every message already delivered.
EXPECTED_ROUTES = {
    "/api/consent/": "consent state and decisions",
    "/api/consent/policy": "the wording the banner renders",
    "/api/consent/records": "the consent register",
    "/api/analytics/collect": "the first-party beacon",
    "/api/analytics/overview": "the analytics dashboard",
    "/api/ads/serve": "advert serving",
    "/api/ads/notices": "site notices",
    "/api/marketing/subscribe": "newsletter sign-up",
    "/api/privacy/requests": "data-subject requests",
    "/api/privacy/me/export": "self-service data export",
}

EXPECTED_COMMANDS = {
    "seed-ads": "festival campaign starter data",
    "seed-privacy": "the retention schedule",
    "seed-privacy-pages": "the privacy, cookie and data-request pages",
    "analytics-rollup": "nightly aggregation",
    "retention-purge": "scheduled deletion",
}


@pytest.fixture(scope="module")
def mounted_paths(app):
    return {str(rule.rule) for rule in app.url_map.iter_rules()}


@pytest.mark.parametrize("path", sorted(EXPECTED_ROUTES))
def test_route_is_mounted_by_the_application(path, mounted_paths):
    assert path in mounted_paths, (
        f"{path} ({EXPECTED_ROUTES[path]}) is not served by create_app(). "
        f"The module exists but is not registered in app/swagger_setup.py, "
        f"so nothing outside the test suite can reach it.")


@pytest.mark.parametrize("command", sorted(EXPECTED_COMMANDS))
def test_cli_command_is_registered_by_the_application(command, app):
    assert command in app.cli.commands, (
        f"`flask {command}` ({EXPECTED_COMMANDS[command]}) is not registered. "
        f"Add its register_*_commands(app) call in app/cli.py.")


def test_no_test_file_mounts_a_namespace_of_its_own():
    """The shims are gone; this keeps them gone.

    A test that registers its own route is testing itself. If a module is
    not mounted yet, the fix is the line in `app/swagger_setup.py`, not a
    workaround at collection time.
    """
    import pathlib

    here = pathlib.Path(__file__).resolve()
    offenders = []
    for file in here.parent.glob("test_*.py"):
        if file == here:
            continue  # this file names the call in order to forbid it
        for number, line in enumerate(
                file.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "add_namespace" in stripped:
                offenders.append(f"{file.name}:{number}")
    assert not offenders, (
        f"test files register their own namespaces at {offenders}; mount the "
        f"module in app/swagger_setup.py instead")


def test_every_public_blueprint_sits_under_api(app):
    """A stray route outside /api escapes the frontend proxy and the audit
    middleware, both of which key off that prefix."""
    allowed_outside = {"/static/<path:filename>", "/swagger.json", "/", "/swaggerui/<path:filename>"}
    stray = sorted(
        str(rule.rule) for rule in app.url_map.iter_rules()
        if not str(rule.rule).startswith("/api")
        and str(rule.rule) not in allowed_outside
    )
    assert not stray, f"routes served outside /api: {stray}"
