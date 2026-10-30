"""Dry-run must never write; a second real run must neither fail nor duplicate."""
import pytest

from conftest import run_batch

CREATES = [r"/v3/sources$", r"/beta/source-apps$", "/v3/access-profiles$", r"/beta/workgroups$"]


@pytest.fixture
def sql_app():
    return {"appName": "Sql-App", "connectorType": "sqlloader", "owner": "asmith", "application": "Sql App",
            "accessProfiles": [{"name": "Sql App - Role", "entitlementValues": ["ROLE-A"]}]}


@pytest.fixture
def all_apps(delim_app, ad_app, sql_app, isc):
    isc.feed = ["ROLE-A"]
    return [delim_app(), sql_app, ad_app()]


def snapshot(isc):
    return {k: len(getattr(isc, k)) for k in ("sources", "apps", "aps", "groups", "ents")}


def test_dry_run_on_empty_tenant_never_writes(isc, defaults, all_apps):
    isc.dry_run = True
    results = run_batch(isc, defaults, all_apps)
    assert isc.writes == [] and snapshot(isc)["sources"] == 1  # only the seeded AD source
    assert [r["status"] for r in results] == ["ok", "ok", "ok"], results


def test_dry_run_on_populated_tenant_never_writes(isc, defaults, all_apps):
    assert {r["status"] for r in run_batch(isc, defaults, all_apps)} == {"ok"}
    before, writes = snapshot(isc), len(isc.writes)
    isc.dry_run = True
    results = run_batch(isc, defaults, all_apps)
    assert len(isc.writes) == writes and snapshot(isc) == before
    assert {r["status"] for r in results} == {"ok"}


def test_dry_run_with_reapply_and_update_flags_never_writes(isc, defaults, delim_app):
    run_batch(isc, defaults, [delim_app()])
    defaults["updateExistingAccessProfiles"] = True
    isc.dry_run, writes = True, len(isc.writes)
    run_batch(isc, defaults, [delim_app(reapplyConfig=True)])
    assert len(isc.writes) == writes


def test_dry_run_still_reports_read_failures(isc, defaults, delim_app):
    isc.dry_run = True
    isc.fail("GET", "public-identities", 503)
    assert run_batch(isc, defaults, [delim_app()])[0]["status"] == "failed"


def test_dry_run_marks_nothing_as_created_in_isc(isc, defaults, delim_app):
    isc.dry_run = True
    r = run_batch(isc, defaults, [delim_app()])[0]
    assert not isc.apps and not isc.aps and "dry-run" in r["note"]


@pytest.mark.parametrize("flags", [{}, {"reapplyConfig": True}], ids=["plain", "reapply"])
def test_rerun_is_idempotent(isc, defaults, all_apps, flags):
    apps = [{**a, **flags} if a["connectorType"] != "ad" else a for a in all_apps]
    run_batch(isc, defaults, apps)
    before = snapshot(isc)
    isc.calls.clear()
    results = run_batch(isc, defaults, apps)
    assert [r["status"] for r in results] == ["ok"] * 3, results
    assert snapshot(isc) == before
    assert not [c for pat in CREATES for c in isc.called("POST", pat)], "second run created something"


def test_rerun_keeps_application_profiles_without_duplicates(isc, defaults, delim_app):
    for _ in range(3):
        run_batch(isc, defaults, [delim_app()])
    (app,) = isc.apps.values()
    assert app["accessProfiles"] == list(isc.aps) and len(app["accessProfiles"]) == 1


def test_rerun_after_partial_failure_completes_the_job(isc, defaults, delim_app):
    isc.fail("POST", "/v3/access-profiles", 500, times=1)
    assert run_batch(isc, defaults, [delim_app()])[0]["status"] == "partial"
    assert run_batch(isc, defaults, [delim_app()])[0]["status"] == "ok"
    assert len(isc.aps) == 1 and len(isc.apps) == 1 and len(isc.sources) == 1


def test_rerun_after_failed_aggregation_reuses_source(isc, defaults, delim_app):
    isc.agg_status = "ERROR"
    assert run_batch(isc, defaults, [delim_app()])[0]["status"] == "failed"
    isc.agg_status = "SUCCESS"
    assert run_batch(isc, defaults, [delim_app()])[0]["status"] == "ok" and len(isc.sources) == 1
