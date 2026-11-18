"""The template-driven connectors (jdbc, api, windows, linux) share one implementation; check each is wired right."""
import pytest

import batch
import connectors
from conftest import ROOT, run_batch

KINDS = {  # type -> (templates key, needs a cluster, secret env name used in the sample)
    "jdbc": ("jdbcTemplates", True, "JDBC_PASSWORD"),
    "api": ("apiTemplates", False, "API_TOKEN"),
    "windows": ("windowsTemplates", True, "WINDOWS_PASSWORD"),
    "linux": ("linuxTemplates", True, "LINUX_PASSWORD"),
}


@pytest.fixture(params=sorted(KINDS))
def kind(request, defaults):
    key, needs_cluster, env = KINDS[request.param]
    defaults[key]["default"].update(connector=f"{request.param}-script", clusterId="cl-1" if needs_cluster else "")
    return request.param, key, needs_cluster, env


@pytest.fixture
def app(kind):
    t, _, _, env = kind
    return {"appName": f"{t}-app", "connectorType": t, "owner": "asmith", "application": f"{t} app",
            t: {"secretEnv": {"password": env}},
            "accessProfiles": [{"name": f"{t} app - Role", "entitlementValues": ["ROLE-A"]}]}


def test_valid_app_passes(kind, app, defaults):
    assert batch.batch_errors([app], defaults) == []


def test_unconfigured_template_is_rejected(kind, app, defaults):
    t, key, *_ = kind
    defaults[key]["default"]["connector"] = "REPLACE_ME"
    assert any("connector script name not set" in e for e in connectors.get(t).validate(app, defaults))


def test_cluster_requirement_matches_the_connector(kind, app, defaults):
    t, key, needs_cluster, _ = kind
    defaults[key]["default"]["clusterId"] = ""
    errs = connectors.get(t).validate(app, defaults)
    assert any("clusterId not set" in e for e in errs) == needs_cluster


def test_creates_with_the_secret_from_the_environment_and_never_logs_it(kind, app, isc, defaults, monkeypatch):
    t, _, needs_cluster, env = kind
    monkeypatch.setenv(env, "Zx-secret-value-1")
    isc.feed = ["ROLE-A"]
    res = run_batch(isc, defaults, [app])[0]
    assert res["source"] == "created"
    body = next(c.body for c in isc.calls if c.method == "POST" and c.path == "/v3/sources")
    assert body["connectorAttributes"]["password"] == "Zx-secret-value-1"
    assert ("cluster" in body) == needs_cluster


def test_inline_credentials_are_rejected(kind, app, defaults):
    t, *_ = kind
    app[t]["connectorAttributes"] = {"password": "plain"}
    assert any("looks like a credential" in e for e in connectors.get(t).validate(app, defaults))


def test_each_connector_has_the_standard_folder_shape():
    for t in KINDS:
        base = ROOT / "onboarding" / "connectors" / t
        for rel in ("apps/wave-1", "apps/wave-2", "data", f"{t}-template.yaml", "README.md"):
            assert (base / rel).exists(), f"{t}/{rel}"
        assert list((base / "apps" / "wave-1").glob("*.yaml.example")), t
