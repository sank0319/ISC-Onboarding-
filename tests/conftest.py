"""Shared fixtures: an in-memory FakeISC that enforces ISC-like rules and injectable faults."""
import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "onboarding" / "scripts"))

import batch  # noqa: E402
import common  # noqa: E402
import isc_client  # noqa: E402
import onboarder  # noqa: E402
from simulator import FakeISC, Fault, Returns, parse_filter  # noqa: E402,F401  (re-exported for the tests)

CSV_TEXT = "EmployeeID,UserName,Email,Status,Role\n1001,u1,u1@example.com,Active,Approver\n1002,u2,u2@example.com,Active,Viewer\n"


class Clock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(onboarder, "time", c)
    monkeypatch.setattr(isc_client, "time", c)
    return c


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """Run logs from main() go to a temp dir, never into the repo's onboarding/logs."""
    monkeypatch.setenv("ISC_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture
def defaults():
    d = common.load_defaults(ROOT / "onboarding" / "config" / "defaults.yaml")
    d["sqlLoaderTemplates"]["default"]["connector"] = "sql-loader-script"
    d["jdbcTemplates"]["default"].update(connector="jdbc-script", clusterId="cl-1")
    return d


@pytest.fixture
def isc():
    f = FakeISC()
    f.add_identity("asmith", "asmith@example.com")
    f.add_identity("jdoe", "jdoe@example.com")
    f.add_identity("twin", "twin@example.com")
    f.add_identity("twin", "twin@example.com")
    for who in ("alice.tan", "bob.lim", "carol.ng"):
        f.add_identity(who, f"{who}@example.com")
    return f


@pytest.fixture
def ob(isc, defaults):
    return onboarder.Onboarder(isc, defaults)


SQL_CSV_TEXT = "USER_ID,USER_NAME,EMAIL,STATUS,ROLE\n2001,u1,u1@example.com,Active,ROLE-A\n2002,u2,u2@example.com,Active,ROLE-A\n"


@pytest.fixture
def csv_file(tmp_path):
    counter = itertools.count()

    def make(text=CSV_TEXT, name=None, raw=None):
        p = tmp_path / (name or f"accounts{next(counter)}.csv")
        p.write_bytes(raw if raw is not None else text.encode("utf-8"))
        return str(p)
    return make


@pytest.fixture
def delim_app(csv_file):
    """Factory for a valid delimited app; overrides are deep-merged (None removes a value)."""
    def make(**over):
        base = {"appName": "Demo-App", "connectorType": "delimited", "owner": "asmith",
                "application": "Demo App",
                "delimited": {"accountFile": csv_file(),
                              "schema": {"identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                                         "entitlementColumns": ["Role"]},
                              "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]},
                "accessProfiles": [{"name": "Demo App - Approver", "entitlementValues": ["Approver"]}]}
        return common.deep_merge(base, over)
    return make


AD_TEXT = ("cn,distinguishedName,description,extensionAttribute10\n"
           'G1,"CN=G1,OU=Treasury,DC=x",d1,<<alice.tan@example.com; bob.lim@example.com>>\n'
           'G2,"CN=G2,OU=Treasury,DC=x",d2,<<carol.ng@example.com>>\n')


@pytest.fixture
def ad_app(csv_file, isc):
    """Factory for a valid AD app; the AD source with matching entitlements is seeded in `isc`."""
    isc.seed_source("Active Directory", "active-directory", ["CN=G1,OU=Treasury,DC=x", "CN=G2,OU=Treasury,DC=x"])

    def make(text=AD_TEXT, **over):
        base = {"appName": "Treasury", "connectorType": "ad", "owner": "asmith", "application": "Treasury",
                "ad": {"sourceName": "Active Directory", "entitlementExport": csv_file(text, "ad.csv")}}
        return common.deep_merge(base, over)
    return make


def run_batch(isc, defaults, apps):
    """validate (as main does) then run every app; returns the per-app results."""
    errors = batch.batch_errors(apps, defaults)
    assert not errors, errors
    ob = onboarder.Onboarder(isc, defaults)
    return [ob.run(a) for a in apps]


@pytest.fixture
def run_one(isc, defaults):
    return lambda app: run_batch(isc, defaults, [app])[0]


@pytest.fixture(autouse=True)
def no_ambient_vault_env(monkeypatch):
    """A developer's own VAULT_* / Actions OIDC variables must not leak into tests."""
    for name in ("VAULT_ADDR", "VAULT_NAMESPACE", "VAULT_ROLE", "VAULT_SECRET_PATH", "VAULT_AUTH_MOUNT",
                 "VAULT_KV_MOUNT", "VAULT_AUDIENCE", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
