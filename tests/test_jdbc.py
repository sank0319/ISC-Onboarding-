"""JDBC connector: validation, secrets only from the environment, and the create/reuse flow."""
import pytest

import batch
import connectors
from conftest import run_batch
from run_log import redact

PASSWORD = "Sup3r-Secret-Pw"


@pytest.fixture
def jdbc_app():
    return {"appName": "Hr-Db", "connectorType": "jdbc", "owner": "asmith", "application": "Hr Db",
            "jdbc": {"connectorAttributes": {"url": "jdbc:postgresql://db:5432/hr", "user": "svc"},
                     "secretEnv": {"password": "JDBC_PASSWORD"}},
            "accessProfiles": [{"name": "Hr Db - Role", "entitlementValues": ["ROLE-A"]}]}


@pytest.fixture
def pw(monkeypatch):
    monkeypatch.setenv("JDBC_PASSWORD", PASSWORD)


def errors(app, defaults):
    return connectors.get("jdbc").validate(app, defaults)


def test_valid_app_passes(jdbc_app, defaults):
    assert errors(jdbc_app, defaults) == []
    assert batch.batch_errors([jdbc_app], defaults) == []


def test_placeholder_connector_and_missing_cluster_are_rejected(jdbc_app, defaults):
    defaults["jdbcTemplates"]["default"]["connector"] = "REPLACE_WITH_JDBC_SCRIPT_NAME"
    defaults["jdbcTemplates"]["default"]["clusterId"] = ""
    errs = errors(jdbc_app, defaults)
    assert any("connector script name not set" in e for e in errs)
    assert any("clusterId not set" in e for e in errs)


def test_global_cluster_id_is_accepted(jdbc_app, defaults):
    defaults["jdbcTemplates"]["default"]["clusterId"] = ""
    defaults["clusterId"] = "cl-global"
    assert errors(jdbc_app, defaults) == []


@pytest.mark.parametrize("tweak,expect", [
    (lambda a: a["jdbc"]["connectorAttributes"].update(password="plain"), "looks like a credential"),
    (lambda a: a["jdbc"]["connectorAttributes"].update(clientSecret="x"), "looks like a credential"),
    (lambda a: a["jdbc"]["connectorAttributes"].update(url="postgres://x"), "must start with 'jdbc:'"),
    (lambda a: a["jdbc"]["secretEnv"].update(password="lower-case"), "environment variable"),
    (lambda a: a.update(template="nope"), "not found in defaults"),
    (lambda a: a["jdbc"].update(secretEnv=["JDBC_PASSWORD"]), "secretEnv must be a mapping"),
])
def test_bad_configs(jdbc_app, defaults, tweak, expect):
    tweak(jdbc_app)
    assert any(expect in e for e in errors(jdbc_app, defaults))


def test_template_schema_and_correlation_are_required(jdbc_app, defaults):
    t = defaults["jdbcTemplates"]["default"]
    t["schema"].pop("attributes")
    t["correlation"] = []
    errs = errors(jdbc_app, defaults)
    assert any("schema.attributes missing" in e for e in errs) and any("correlation missing" in e for e in errs)


def test_creates_source_on_the_cluster_with_the_password_from_the_environment(isc, defaults, jdbc_app, pw):
    isc.feed = ["ROLE-A"]  # what the appliance's aggregation would load
    res = run_batch(isc, defaults, [jdbc_app])[0]
    assert res["source"] == "created" and res["entitlementsRequestable"] == 1
    body = next(c.body for c in isc.calls if c.method == "POST" and c.path == "/v3/sources")
    assert body["connector"] == "jdbc-script" and body["cluster"]["id"] == "cl-1"
    assert body["connectorAttributes"]["password"] == PASSWORD
    assert body["connectorAttributes"]["url"] == "jdbc:postgresql://db:5432/hr"
    assert PASSWORD not in redact(str(body))  # registered, so it is masked in logs


def test_missing_environment_variable_fails_before_creating_anything(isc, defaults, jdbc_app, monkeypatch):
    monkeypatch.delenv("JDBC_PASSWORD", raising=False)
    res = run_batch(isc, defaults, [jdbc_app])[0]
    assert "JDBC_PASSWORD" in str(res) and not isc.sources


def test_rerun_reuses_the_source_without_needing_the_password(isc, defaults, jdbc_app, pw, monkeypatch):
    isc.feed = ["ROLE-A"]
    run_batch(isc, defaults, [jdbc_app])
    monkeypatch.delenv("JDBC_PASSWORD")
    res = run_batch(isc, defaults, [jdbc_app])[0]
    assert res["source"] == "existing"
    assert len([c for c in isc.calls if c.method == "POST" and c.path == "/v3/sources"]) == 1


def test_dry_run_creates_nothing_and_needs_no_password(isc, defaults, jdbc_app, monkeypatch):
    monkeypatch.delenv("JDBC_PASSWORD", raising=False)
    isc.dry_run = True
    res = run_batch(isc, defaults, [jdbc_app])[0]
    assert res["source"] == "created" and not isc.sources and not isc.writes


def test_jdbc_has_no_feed_file(jdbc_app):
    assert connectors.get("jdbc").feed(jdbc_app) is None
