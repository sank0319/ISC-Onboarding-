"""API failures at every pipeline step: app must be failed/partial, the batch must go on."""
import pytest
import requests

from conftest import Returns, run_batch

# step -> (method, path regex, fault kwargs, expected status, source already created?)
STEPS = {
    "owner-lookup": ("GET", "public-identities", {}, "failed", False),
    "source-lookup": ("GET", r"/v3/sources$", {}, "failed", False),
    "source-create": ("POST", r"/v3/sources$", {}, "failed", False),
    "schema-get": ("GET", "/schemas$", {}, "failed", True),
    "schema-put": ("PUT", "/schemas/", {}, "failed", True),
    "correlation-get": ("GET", "correlation-config", {}, "failed", True),
    "correlation-put": ("PUT", "correlation-config", {}, "failed", True),
    "upload": ("POST", "load-accounts", {}, "failed", True),
    "task-poll": ("GET", "task-status", {}, "failed", True),
    "entitlement-list": ("GET", "/beta/entitlements$", {}, "failed", True),
    "bulk-update": ("POST", "bulk-update", {}, "failed", True),
    "request-config": ("PUT", "entitlement-request-config", {}, "failed", True),
    "app-lookup": ("GET", "/beta/source-apps/all", {}, "failed", True),
    "app-create": ("POST", r"/beta/source-apps$", {}, "failed", True),
    "app-patch-first": ("PATCH", "/beta/source-apps/", {}, "failed", True),
    "ap-create": ("POST", "/v3/access-profiles", {}, "partial", True),
    "app-patch-second": ("PATCH", "/beta/source-apps/", {"after": 1}, "partial", True),
}


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("code", [400, 404, 409, 429, 500, 503])
def test_failure_at_each_step_is_reported_and_batch_continues(isc, defaults, delim_app, step, code):
    method, pattern, kw, status, created = STEPS[step]
    isc.fail(method, pattern, code, times=1, **kw)
    bad, good = delim_app(), delim_app(appName="Good-App", application="Good App",
                                       accessProfiles=[{"name": "Good AP", "entitlementValues": ["Approver"]}])
    r1, r2 = run_batch(isc, defaults, [bad, good])
    assert r1["status"] == status, r1
    assert str(code) in (r1.get("error") or r1.get("accessProfileError")), r1
    assert (r1.get("source") == "created") is created, "leftover source must be reported"
    assert r2["status"] == "ok" and "Good-App" in {s["name"] for s in isc.sources.values()}


@pytest.mark.parametrize("effect", [requests.Timeout("t/o"), requests.ConnectionError("reset"), ValueError("bad json"),
                                    KeyError("id"), Returns("<html>oops</html>"), Returns({"unexpected": 1}),
                                    Returns(None), Returns({})], ids=lambda e: type(e).__name__ + str(getattr(e, "value", "")))
@pytest.mark.parametrize("path", ["/v3/sources$", r"/beta/source-apps$", "/v3/access-profiles$"])
def test_exceptions_and_malformed_bodies_never_pass_as_ok(isc, defaults, delim_app, effect, path):
    isc.fail("POST", path, effect, times=1)
    first, second = run_batch(isc, defaults, [delim_app(), delim_app(appName="B", application="B2",
                                                                     accessProfiles=[{"name": "AP-B", "entitlementValues": ["Viewer"]}])])
    assert first["status"] != "ok", first
    assert second["status"] == "ok"


@pytest.mark.parametrize("path", [r"/beta/entitlements$", "/schemas$", "/beta/source-apps/all", "public-identities"])
def test_empty_list_responses(isc, defaults, run_one, delim_app, path):
    isc.fail("GET", path, Returns([]))
    assert run_one(delim_app())["status"] != "ok"


def test_access_profile_lookup_outage_falls_back_to_create(isc, run_one, delim_app):
    isc.fail("GET", "/v3/access-profiles", 503)
    assert run_one(delim_app())["status"] == "ok" and len(isc.aps) == 1


def test_lookup_outage_for_existing_profile_is_partial_not_duplicate(isc, run_one, delim_app):
    assert run_one(delim_app())["status"] == "ok"
    isc.fail("GET", "/v3/access-profiles", 503)
    r = run_one(delim_app())
    assert r["status"] == "partial" and "accessProfileError" in r and len(isc.aps) == 1


def test_access_profile_failure_keeps_application(isc, run_one, delim_app):
    isc.fail("POST", "/v3/access-profiles", 500)
    r = run_one(delim_app())
    assert r["status"] == "partial" and [a["name"] for a in isc.apps.values()] == ["Demo App"]


# ---- aggregation task handling
def test_aggregation_waits_for_slow_task(isc, run_one, delim_app, clock):
    isc.agg_polls = 3
    assert run_one(delim_app())["status"] == "ok" and clock.sleeps == [15, 15, 15]


def test_aggregation_that_never_finishes_times_out(isc, run_one, delim_app, defaults, clock):
    isc.agg_polls, defaults["aggregationTimeoutMinutes"] = 10**9, 2
    r = run_one(delim_app())
    assert r["status"] == "failed" and "timed out" in r["error"] and len(clock.sleeps) <= 9


@pytest.mark.parametrize("status,ok", [("SUCCESS", True), ("WARNING", True), ("ERROR", False),
                                       ("TERMINATED", False), ("TEMP_ERROR", False)])
def test_aggregation_completion_status(isc, run_one, delim_app, status, ok):
    isc.agg_status = status
    r = run_one(delim_app())
    assert (r["status"] == "ok") is ok and (ok or "Aggregation failed" in r["error"])


def test_failed_aggregation_leaves_source_but_reports_it(isc, run_one, delim_app):
    isc.agg_status = "ERROR"
    r = run_one(delim_app())
    assert r["source"] == "created" and len(isc.sources) == 1 and not isc.apps
