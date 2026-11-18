"""In-memory stand-in for the ISC API: the same interface as ISCClient (request/get_all/patch/dry_run) with
ISC-like rules (required fields, unique names, filters, paging). Used by the tests and by `onboard.py --offline`,
which rehearses a whole batch without a tenant."""
import csv
import io
import itertools
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace

from isc_client import ISCClient, ISCError

MAX_NAME = 128
FILTER_RE = re.compile(r'^([\w.]+) eq "((?:[^"\\]|\\.)*)"$')


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
    base, client_id, client_secret = "https://t.api.example.com", "fake-client-id-1234", "fake-client-secret-5678"
    _emit = ISCClient._emit
    on_event = None

    def __init__(self):
        self.dry_run = False
        self.calls, self.writes, self.faults = [], [], []
        self._ids = itertools.count(1)
        self.identities, self.sources, self.schemas, self.ents = {}, {}, {}, {}
        self.apps, self.aps, self.groups, self.tasks, self.corr = {}, {}, {}, {}, {}
        self.agg_polls, self.agg_status, self.feed = 0, "SUCCESS", []
        self.loaded = set()  # source ids that received an account feed
        self.routes = [(m, re.compile(p + "$"), h) for m, p, h in [
            ("GET", "/v3/public-identities", self.list_identities),
            ("GET", "/v3/sources", self.list_sources), ("POST", "/v3/sources", self.create_source),
            ("GET", "/v3/sources/([^/]+)/schemas", self.get_schemas),
            ("PUT", "/v3/sources/([^/]+)/schemas/([^/]+)", self.put_schema),
            ("GET", "/beta/sources/([^/]+)/correlation-config", self.get_corr),
            ("PUT", "/beta/sources/([^/]+)/correlation-config", self.put_corr),
            ("POST", "/v2024/sources/([^/]+)/load-accounts", self.load_accounts),
            ("GET", "/v3/accounts", lambda r: [{"id": "acct"}] if any(
                f'"{sid}"' in (r.params or {}).get("filters", "") for sid in self.loaded) else []),
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
        self.loaded.add(sid)
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


# ------------------------------------------------------------------ offline rehearsal (onboard.py --offline)
def fill_placeholders(defaults):
    """(defaults copy, notes): template connectors still set to REPLACE_* get a simulated script name and cluster,
    and every secretEnv variable gets a dummy value, so a rehearsal can run before the tenant values are known."""
    import copy
    import os
    out, notes = copy.deepcopy(defaults), []
    for key, group in out.items():
        if not (key.endswith("Templates") and isinstance(group, dict)):
            continue
        for name, tpl in group.items():
            if not isinstance(tpl, dict):
                continue
            if isinstance(tpl.get("connector"), str) and tpl["connector"].startswith("REPLACE"):
                tpl["connector"] = f"offline-{key[:-len('Templates')].lower()}-connector"
                if not tpl.get("clusterId") and not out.get("clusterId"):
                    tpl["clusterId"] = "offline-cluster"
                notes.append(f"{key}.{name}: placeholder connector replaced by '{tpl['connector']}'")
            for env in (tpl.get("secretEnv") or {}).values():
                if isinstance(env, str) and env and not os.environ.get(env):
                    os.environ[env] = "offline-placeholder"
    return out, notes


def rehearsal_isc(apps, defaults, dry_run=False):
    """A FakeISC seeded with what the real tenant would already hold for these apps: the owners (and AD approvers)
    as identities, AD sources with their groups as entitlements, and the entitlements an appliance read would load."""
    from common import read_csv
    from connectors.ad import parse_approvers
    sim = FakeISC()
    sim.dry_run = dry_run
    seen = set()

    def identity(key):
        if key and key.lower() not in seen:
            seen.add(key.lower())
            sim.add_identity(key.split("@")[0], key if "@" in key else f"{key}@offline.invalid")

    adc = defaults.get("ad") or {}
    identity(adc.get("governanceGroupOwner"))
    import os
    for app in apps:
        identity(app.get("owner"))
        for section in app.values():  # secretEnv set on the app itself: dummy values keep the rehearsal offline
            for env in (section.get("secretEnv") or {}).values() if isinstance(section, dict) else ():
                if isinstance(env, str) and env and not os.environ.get(env):
                    os.environ[env] = "offline-placeholder"
        sim.feed += [v for ap in app.get("accessProfiles") or [] for v in ap.get("entitlementValues") or []]
        ad = app.get("ad")
        if app.get("connectorType") != "ad" or not isinstance(ad, dict):
            continue
        identity(ad.get("governanceGroupOwner"))
        vcol, acol = ad.get("valueColumn", adc.get("valueColumn")), ad.get("approvalAttribute", adc.get("approvalAttribute"))
        _, rows = read_csv(ad["entitlementExport"], ad.get("delimiter", ","))
        for r in rows:
            try:
                for email in parse_approvers(r.get(acol)):
                    identity(email)
            except ValueError:
                pass  # validation reports it; the rehearsal then shows the same skip a real run would
        if not any(s["name"] == ad["sourceName"] for s in sim.sources.values()):
            sim.seed_source(ad["sourceName"], "active-directory", [r[vcol] for r in rows if r.get(vcol)])
    return sim
