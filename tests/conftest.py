"""Shared fixtures: an in-memory FakeISC that enforces ISC-like rules and injectable faults."""
import csv
import io
import itertools
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "onboarding" / "scripts"))

import isc_client  # noqa: E402
import onboard  # noqa: E402
from isc_client import ISCClient, ISCError  # noqa: E402

MAX_NAME = 128
FILTER_RE = re.compile(r'^([\w.]+) eq "((?:[^"\\]|\\.)*)"$')
CSV_TEXT = "EmployeeID,UserName,Email,Status,Role\n1001,u1,u1@example.com,Active,Approver\n1002,u2,u2@example.com,Active,Viewer\n"


def parse_filter(expr):
    """Parse `field eq "value"` the way ISC does (backslash escapes); None if malformed."""
    m = FILTER_RE.match(expr or "")
    return (m[1], re.sub(r"\\(.)", r"\1", m[2])) if m else None


class Returns:
    def __init__(self, value):
        self.value = value


@dataclass
class Fault:
    method: str
    pattern: str
    effect: object
    times: int | None = None
    after: int = 0
    seen: int = 0


class FakeISC:
    """Same interface as ISCClient (request/get_all/patch/dry_run). Faults model the *final*
    outcome after the client's own retries, e.g. fail("POST", "/v3/sources", 503)."""
    get_all = ISCClient.get_all
    patch = ISCClient.patch
    _emit = ISCClient._emit
    on_event = None

    def __init__(self):
        self.dry_run = False
        self.calls, self.writes, self.faults = [], [], []
        self._ids = itertools.count(1)
        self.identities, self.sources, self.schemas, self.ents = {}, {}, {}, {}
        self.apps, self.aps, self.groups, self.tasks, self.corr = {}, {}, {}, {}, {}
        self.agg_polls, self.agg_status, self.feed = 0, "SUCCESS", []
        self.routes = [(m, re.compile(p + "$"), h) for m, p, h in [
            ("GET", "/v3/public-identities", self.list_identities),
            ("GET", "/v3/sources", self.list_sources), ("POST", "/v3/sources", self.create_source),
            ("GET", "/v3/sources/([^/]+)/schemas", self.get_schemas),
            ("PUT", "/v3/sources/([^/]+)/schemas/([^/]+)", self.put_schema),
            ("GET", "/beta/sources/([^/]+)/correlation-config", self.get_corr),
            ("PUT", "/beta/sources/([^/]+)/correlation-config", self.put_corr),
            ("POST", "/v2024/sources/([^/]+)/load-accounts", self.load_accounts),
            ("GET", "/beta/task-status/([^/]+)", self.task_status),
            ("GET", "/beta/entitlements", lambda r: self.page(self.ents.values(), r)),
            ("POST", "/beta/entitlements/bulk-update", self.bulk_update),
            ("PUT", "/beta/entitlements/([^/]+)/entitlement-request-config", self.put_ent_cfg),
            ("GET", "/v3/access-profiles", lambda r: self.page(self.aps.values(), r)),
            ("POST", "/v3/access-profiles", self.create_ap),
            ("PATCH", "/v3/access-profiles/([^/]+)", self.patch_ap),
            ("GET", "/beta/source-apps", lambda r: self.page([a for a in self.apps.values() if a["accessProfiles"]], r)),
            ("GET", "/beta/source-apps/all", lambda r: self.page(self.apps.values(), r)),
            ("POST", "/beta/source-apps", self.create_app),
            ("GET", "/beta/source-apps/([^/]+)/access-profiles", self.app_aps),
            ("PATCH", "/beta/source-apps/([^/]+)", self.patch_app),
            ("GET", "/beta/workgroups", lambda r: self.page(self.groups.values(), r)),
            ("POST", "/beta/workgroups", self.create_group),
            ("GET", "/beta/workgroups/([^/]+)/members", self.group_members),
            ("POST", "/beta/workgroups/([^/]+)/members/bulk-add", self.members_add),
            ("POST", "/beta/workgroups/([^/]+)/members/bulk-delete", self.members_delete),
        ]]

    # ---- test-facing helpers
    def fail(self, method, pattern, effect=500, *, times=None, after=0):
        self.faults.append(Fault(method, pattern, effect, times, after))

    def add_identity(self, alias, email=None, name=None):
        iid = self.new_id("id")
        self.identities[iid] = {"id": iid, "alias": alias, "email": email, "name": name or alias}
        return iid

    def seed_source(self, name, connector="delimited-file-angularsc", values=()):
        src = self.create_source(self.req("POST", "/v3/sources", body={
            "name": name, "description": "seeded", "connector": connector, "owner": {"id": "x"}}))
        for v in values:
            self.add_entitlement(src["id"], v)
        return src

    def add_entitlement(self, source_id, value, attr="Role"):
        eid = self.new_id("ent")
        self.ents[eid] = {"id": eid, "value": value, "name": value, "attribute": attr,
                          "requestable": False, "source": {"id": source_id}}
        return eid

    def called(self, method=None, pattern=""):
        return [c for c in self.calls if (method in (None, c.method)) and re.search(pattern, c.path)]

    # ---- request plumbing
    def new_id(self, prefix):
        return f"{prefix}{next(self._ids)}"

    @staticmethod
    def req(method, path, params=None, body=None, files=None, data=None, m=None):
        return SimpleNamespace(method=method, path=path, params=params or {}, body=body or {},
                               files=files, data=data, m=m)

    def request(self, method, path, *, params=None, json_body=None, data=None, files=None,
                headers=None, write=False, retries=10):
        self.calls.append(SimpleNamespace(method=method, path=path, params=params, body=json_body or data,
                                          write=write, retries=retries))
        if write and self.dry_run:
            return None
        if method != "GET":
            self.writes.append((method, path))
        for f in self.faults:
            if f.method in (method, "*") and re.search(f.pattern, path):
                f.seen += 1
                if f.seen > f.after and (f.times is None or f.seen <= f.after + f.times):
                    return self.inject(f, method, path)
        for m, rx, handler in self.routes:
            if m == method and (hit := rx.match(path)):
                body = json_body if json_body is not None else data
                if isinstance(body, str):
                    body = json.loads(body)
                return handler(self.req(method, path, params, body, files, data, hit))
        raise ISCError(f"{method} {path} -> 404: no such endpoint")

    @staticmethod
    def inject(fault, method, path):
        e = fault.effect
        if isinstance(e, Returns):
            return e.value
        if isinstance(e, BaseException):
            raise e
        raise ISCError(f"{method} {path} -> {e}: injected")

    def bad(self, r, code, msg):
        raise ISCError(f"{r.method} {r.path} -> {code}: {msg}")

    # ---- generic rules
    def need(self, r, *fields):
        for f in fields:
            v = r.body.get(f)
            if v is None or (isinstance(v, (str, list)) and not str(v).strip()):
                self.bad(r, 400, f"Required field {f} was missing or empty")
        name = r.body.get("name")
        if name is not None and len(name) > MAX_NAME:
            self.bad(r, 400, f"name exceeds {MAX_NAME} characters")

    def unique(self, r, coll, label="object"):
        name = r.body["name"].strip().casefold()
        if any(o["name"].strip().casefold() == name for o in coll.values()):
            self.bad(r, 400, f'"{label}" with "name" "{r.body["name"]}" already exists')

    def page(self, items, r):
        items = list(items)
        if "filters" in r.params:
            f = parse_filter(r.params["filters"])
            if not f:
                self.bad(r, 400, f"invalid filter: {r.params['filters']}")
            items = [i for i in items if self.attr(i, f[0]) == f[1]]
        off, lim = int(r.params.get("offset", 0)), int(r.params.get("limit", 250))
        return [dict(i) for i in items[off:off + lim]]

    @staticmethod
    def attr(item, dotted):
        for part in dotted.split("."):
            item = (item or {}).get(part)
        return item

    # ---- endpoints
    def list_identities(self, r):
        return self.page(self.identities.values(), r)

    def list_sources(self, r):
        return self.page(self.sources.values(), r)

    def create_source(self, r):
        self.need(r, "name", "description", "connector", "owner")
        self.unique(r, self.sources, "source")
        sid = self.new_id("src")
        self.sources[sid] = {**r.body, "id": sid}
        self.schemas[f"sch{sid}"] = {"id": f"sch{sid}", "sourceId": sid, "name": "account",
                                     "nativeObjectType": "account", "attributes": []}
        return dict(self.sources[sid])

    def get_schemas(self, r):
        return [dict(s) for s in self.schemas.values() if s["sourceId"] == r.m[1]]

    def put_schema(self, r):
        names = [a.get("name") for a in r.body["attributes"]]
        if any(not n for n in names) or len(set(names)) != len(names):
            self.bad(r, 400, "attribute names must be non-empty and unique")
        for k in ("identityAttribute", "displayAttribute"):
            if r.body.get(k) not in names:
                self.bad(r, 400, f"{k} '{r.body.get(k)}' is not a schema attribute")
        self.schemas[r.m[2]] = {**r.body, "sourceId": r.m[1]}

    def get_corr(self, r):
        return dict(self.corr.get(r.m[1], {}))

    def put_corr(self, r):
        self.corr[r.m[1]] = r.body

    def load_accounts(self, r):
        sid = r.m[1]
        text = r.files["file"][1].read().decode("utf-8-sig") if r.files else ""
        rows = list(csv.DictReader(io.StringIO(text)))
        if r.files and not rows:
            self.bad(r, 400, "account file has no rows")
        known = {e["value"] for e in self.ents.values() if e["source"]["id"] == sid}
        for v in ([] if r.files else [v for v in self.feed if v not in known]):
            self.add_entitlement(sid, v)
        schema = next(s for s in self.schemas.values() if s["sourceId"] == sid)
        for a in (a for a in schema["attributes"] if a.get("isEntitlement")):
            for v in {row.get(a["name"]) for row in rows} - {None, ""}:
                if not any(e["value"] == v and e["source"]["id"] == sid for e in self.ents.values()):
                    self.add_entitlement(sid, v, a["name"])
        tid = self.new_id("task")
        self.tasks[tid] = self.agg_polls
        return {"task": {"id": tid}}

    def task_status(self, r):
        if self.tasks[r.m[1]] > 0:
            self.tasks[r.m[1]] -= 1
            return {"completed": None}
        return {"completed": "now", "completionStatus": self.agg_status, "messages": ["boom"]}

    def bulk_update(self, r):
        ids = r.body["entitlementIds"]
        if not 0 < len(ids) <= 50 or any(i not in self.ents for i in ids):
            self.bad(r, 400, "entitlementIds must hold 1-50 existing ids")
        for i in ids:
            self.ents[i]["requestable"] = True

    def put_ent_cfg(self, r):
        if r.m[1] not in self.ents:
            self.bad(r, 404, "entitlement not found")
        self.ents[r.m[1]]["requestConfig"] = r.body

    def create_ap(self, r):
        self.need(r, "name", "description", "owner", "source", "entitlements")
        self.unique(r, self.aps, "accessProfile")
        if any(e["id"] not in self.ents for e in r.body["entitlements"]):
            self.bad(r, 400, "unknown entitlement")
        aid = self.new_id("ap")
        self.aps[aid] = {**r.body, "id": aid}
        return dict(self.aps[aid])

    def patch_ap(self, r):
        for op in r.body:
            self.aps[r.m[1]][op["path"].lstrip("/")] = op["value"]

    def create_app(self, r):
        self.need(r, "name", "description", "accountSource")
        self.unique(r, self.apps, "cloudApp")
        aid = self.new_id("app")
        self.apps[aid] = {**r.body, "id": aid, "accessProfiles": [], "enabled": False, "owner": None}
        return dict(self.apps[aid])

    def app_aps(self, r):
        return [dict(self.aps[i]) for i in self.apps[r.m[1]]["accessProfiles"]]

    def patch_app(self, r):
        app = self.apps.get(r.m[1]) or self.bad(r, 404, "application not found")
        for op in r.body:
            key, val = op["path"].lstrip("/"), op["value"]
            if key == "accessProfiles" and any(i not in self.aps for i in val):
                self.bad(r, 400, "unknown access profile")
            app[key] = val

    def create_group(self, r):
        self.need(r, "name", "description", "owner")
        self.unique(r, self.groups, "workgroup")
        gid = self.new_id("gg")
        self.groups[gid] = {**r.body, "id": gid, "members": []}
        return dict(self.groups[gid])

    def group_members(self, r):
        return self.page([{"id": i} for i in self.groups[r.m[1]]["members"]], r)

    def members_add(self, r):
        ids = [m["id"] for m in r.body]
        if any(i not in self.identities for i in ids):
            self.bad(r, 400, "unknown identity")
        self.groups[r.m[1]]["members"] += [i for i in ids if i not in self.groups[r.m[1]]["members"]]

    def members_delete(self, r):
        gone = {m["id"] for m in r.body}
        self.groups[r.m[1]]["members"] = [i for i in self.groups[r.m[1]]["members"] if i not in gone]


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
    monkeypatch.setattr(onboard, "time", c)
    monkeypatch.setattr(isc_client, "time", c)
    return c


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """Run logs from main() go to a temp dir, never into the repo's onboarding/logs."""
    monkeypatch.setenv("ISC_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture
def defaults():
    d = onboard.load_yaml(ROOT / "onboarding" / "config" / "defaults.yaml")
    d["sqlLoaderTemplates"]["default"]["connector"] = "sql-loader-script"
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
    return onboard.Onboarder(isc, defaults)


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
        return onboard.deep_merge(base, over)
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
        return onboard.deep_merge(base, over)
    return make


def run_batch(isc, defaults, apps):
    """validate (as main does) then run every app; returns the per-app results."""
    errors = onboard.batch_errors(apps, defaults)
    assert not errors, errors
    ob = onboard.Onboarder(isc, defaults)
    return [ob.run(a) for a in apps]


@pytest.fixture
def run_one(isc, defaults):
    return lambda app: run_batch(isc, defaults, [app])[0]
