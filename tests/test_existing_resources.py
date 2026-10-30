"""Names that already exist in ISC: reuse when it is ours, refuse when it is not."""
from conftest import run_batch


def test_existing_source_of_same_connector_is_reused(isc, run_one, delim_app, defaults):
    isc.seed_source("Demo-App", defaults["connectors"]["delimited"])
    r = run_one(delim_app())
    assert r["source"] == "existing" and len(isc.sources) == 1 and not isc.called("POST", r"/v3/sources$")


def test_existing_source_of_other_connector_is_refused(isc, run_one, delim_app):
    isc.seed_source("Demo-App", "active-directory")
    r = run_one(delim_app())
    assert r["status"] == "failed" and not isc.called("POST", "load-accounts")


def test_source_name_differing_only_by_case_is_reported(isc, run_one, delim_app, defaults):
    isc.seed_source("DEMO-APP", defaults["connectors"]["delimited"])
    r = run_one(delim_app())
    assert r["status"] == "failed" and "already exists" in r["error"] and len(isc.sources) == 1


def test_existing_application_without_profiles_is_found_not_duplicated(isc, run_one, delim_app):
    src = isc.seed_source("Demo-App", "delimited-file-angularsc", ["Approver"])
    isc.apps["a0"] = {"id": "a0", "name": "Demo App", "accessProfiles": [], "accountSource": {"id": src["id"]}}
    assert run_one(delim_app())["status"] == "ok"
    assert list(isc.apps) == ["a0"] and not isc.called("POST", r"/beta/source-apps$")
    assert not isc.called("GET", r"/beta/source-apps$"), "GET /source-apps hides apps without access profiles"


def test_application_owned_by_another_source_is_refused(isc, run_one, delim_app):
    other = isc.seed_source("Other Source")
    isc.apps["a0"] = {"id": "a0", "name": "Demo App", "accessProfiles": [], "accountSource": {"id": other["id"]},
                      "owner": "keep"}
    r = run_one(delim_app())
    assert r["status"] == "failed" and isc.apps["a0"]["owner"] == "keep" and not isc.aps


def test_application_name_differing_by_case_fails_cleanly(isc, run_one, delim_app):
    isc.apps["a0"] = {"id": "a0", "name": "DEMO APP", "accessProfiles": [], "accountSource": {"id": "s"}}
    r = run_one(delim_app())
    assert r["status"] == "failed" and "already exists" in r["error"] and len(isc.apps) == 1


def test_access_profile_of_another_source_is_not_reused(isc, run_one, delim_app):
    other = isc.seed_source("Other Source")
    isc.aps["ap0"] = {"id": "ap0", "name": "Demo App - Approver", "source": {"id": other["id"]}, "entitlements": []}
    r = run_one(delim_app())
    assert r["status"] == "partial" and isc.apps and list(isc.apps.values())[0]["accessProfiles"] == []


def test_same_access_profile_name_in_two_apps_second_is_reported(isc, defaults, delim_app):
    apps = [delim_app(), delim_app(appName="B", application="B App")]
    first, second = run_batch(isc, defaults, apps)
    assert first["status"] == "ok" and second["status"] == "partial" and len(isc.aps) == 1


def test_updating_existing_profile_only_when_enabled(isc, run_one, delim_app, defaults):
    run_one(delim_app())
    assert not isc.called("PATCH", "/v3/access-profiles")
    defaults["updateExistingAccessProfiles"] = True
    run_one(delim_app())
    assert len(isc.called("PATCH", "/v3/access-profiles")) == 1 and len(isc.aps) == 1
