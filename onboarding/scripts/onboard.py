#!/usr/bin/env python3
"""Bulk onboarding of applications into SailPoint ISC (run from GitHub Actions).

Connector handlers
  sqlloader : create source from template (schema + correlation predefined), aggregate,
              make entitlements requestable with approvals L1 Manager -> L2 Source Owner.
  delimited : create source, per-app schema + correlation, upload account file, aggregate,
              make entitlements requestable with approvals L1 Manager -> L2 Source Owner.
  ad        : no source creation. Read entitlement export; per in-scope entitlement:
              create a governance group named after the access profile, add the approvers
              from the extension attribute (<<email1; email2>>), create the access profile
              with approvals L1 Manager -> L2 that governance group, attach to the application.

Usage
  python onboard.py --apps "onboarding/apps/*.yaml" --validate-only
  python onboard.py --apps "onboarding/apps/*.yaml" --dry-run
  python onboard.py --apps "onboarding/apps/*.yaml"
"""
import argparse
import copy
import csv
import glob
import json
import logging
import os
import re
import sys
import time

import yaml

from isc_client import ISCClient, ISCError
from run_log import RunLog

log = logging.getLogger("onboard")

VALID_TYPES = {"sqlloader", "delimited", "ad"}

# Non-AD requirement: Level 1 Manager, Level 2 Source Owner
NON_AD_APPROVALS = [
    {"approverType": "MANAGER", "approverId": None},
    {"approverType": "SOURCE_OWNER", "approverId": None},
]

MAX_NAME = 128  # assumed ISC limit (same as the governance group default); verify per tenant
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
EMAIL_RE = re.compile(r"^[^@\s;<>]+@[^@\s;<>]+\.[^@\s;<>]+$")


# ---------------------------------------------------------------- helpers
def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base, override):
    out = copy.deepcopy(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def esc(v):
    """Escape a value for an ISC filter string literal (backslash first, then quote)."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def norm(name):
    return " ".join(name.split()).casefold()


def text_or(value, default):
    return value if isinstance(value, str) and value.strip() else default


def name_problem(v):
    if not isinstance(v, str) or not v.strip():
        return "must be a non-empty string"
    if v != v.strip() or CTRL_RE.search(v):
        return "must not have leading/trailing whitespace or control characters"
    if len(v) > MAX_NAME:
        return f"must be at most {MAX_NAME} characters"


def read_csv(path, delimiter=","):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        return reader.fieldnames or [], list(reader)


def parse_approvers(raw):
    """AD extension attribute '<<a@x.com; b@x.com>>' -> ['a@x.com', 'b@x.com'] (deduped, lower-case).

    Raises ValueError if empty or if any entry is not a valid email address.
    """
    cleaned = re.sub(r"[<>]", "", raw or "").strip()
    emails = list(dict.fromkeys(e.strip().lower() for e in cleaned.split(";") if e.strip()))
    if not emails:
        raise ValueError("approver attribute is empty - level 2 approver is mandatory")
    bad = [e for e in emails if not EMAIL_RE.match(e)]
    if bad:
        raise ValueError(f"invalid approver email(s): {bad}")
    return emails


def gov_group_name(ap_name, max_len):
    name = re.sub(r"\s+", " ", ap_name).strip()
    return name[:max_len].rstrip() if max_len else name


# ---------------------------------------------------------------- validation (no API calls)
def check_text(errs, obj, key, label=None, required=True):
    v = obj.get(key)
    if v is None or (required and v == ""):
        if required:
            errs.append(f"{label or key} is required")
    elif problem := name_problem(v):
        errs.append(f"{label or key} {problem}")


def read_csv_checked(path, delimiter, what):
    """Returns (columns, rows, errors) without ever raising on bad files."""
    if not isinstance(path, str) or not os.path.isfile(path):
        return [], [], [f"{what} not found: {path}"]
    try:
        cols, rows = read_csv(path, delimiter)
    except (OSError, UnicodeDecodeError, csv.Error, TypeError) as e:
        return [], [], [f"{what} unreadable: {e}"]
    if not cols:
        return [], [], [f"{what} has no header row: {path}"]
    return cols, rows, []


def access_profile_problems(aps):
    if aps is None:
        return []
    if not isinstance(aps, list):
        return ["accessProfiles must be a list"]
    errs, seen = [], set()
    for i, ap in enumerate(aps):
        if not isinstance(ap, dict):
            errs.append(f"accessProfiles[{i}] must be a mapping")
            continue
        if problem := name_problem(ap.get("name")):
            errs.append(f"accessProfiles[{i}].name {problem}")
        elif norm(ap["name"]) in seen:
            errs.append(f"accessProfiles[{i}] duplicate name: {ap['name']}")
        else:
            seen.add(norm(ap["name"]))
        vals = ap.get("entitlementValues")
        if not (isinstance(vals, list) and vals and all(isinstance(v, str) and v.strip() for v in vals)):
            errs.append(f"accessProfiles[{i}].entitlementValues must be a non-empty list of strings")
    return errs


def delimited_problems(dl, d):
    errs, sch, corr = [], dl.get("schema") or {}, dl.get("correlation")
    for k in ("identityAttribute", "displayAttribute"):
        if not isinstance(sch.get(k), str) or not sch[k]:
            errs.append(f"delimited.schema.{k} missing")
    ent = sch.get("entitlementColumns")
    if not ent:
        errs.append("delimited.schema.entitlementColumns missing")
    elif not (isinstance(ent, list) and all(isinstance(c, str) for c in ent)):
        errs.append("delimited.schema.entitlementColumns must be a list of column names")
    if not corr:
        errs.append("delimited.correlation missing (needs human sign-off)")
    elif not (isinstance(corr, list) and all(isinstance(r, dict) and r.get("identityAttr") and r.get("accountAttr")
                                             for r in corr)):
        errs.append("delimited.correlation entries need identityAttr and accountAttr")
    if not dl.get("accountFile"):
        return errs + ["delimited.accountFile missing"]
    delim = deep_merge(d.get("delimitedConnectorAttributes"), dl.get("connectorAttributes")).get("delimiter", ",")
    header, rows, file_errs = read_csv_checked(dl["accountFile"], delim, "delimited.accountFile")
    if file_errs:
        return errs + file_errs
    if any(not c.strip() for c in header):
        errs.append("accountFile header has a blank column name")
    if len(set(header)) != len(header):
        errs.append(f"accountFile header has duplicate columns: {sorted({c for c in header if header.count(c) > 1})}")
    if not rows:
        errs.append("accountFile has no account rows")
    columns = sch.get("attributes") or header
    wanted = [sch.get("identityAttribute"), sch.get("displayAttribute"), *(ent if isinstance(ent, list) else [])]
    wanted += [r["accountAttr"] for r in (corr if isinstance(corr, list) else [])
               if isinstance(r, dict) and r.get("accountAttr")]
    errs += [f"column '{c}' not in accountFile header {header}" for c in dict.fromkeys(wanted)
             if isinstance(c, str) and c and c not in columns]
    return errs


def ad_problems(app, d):
    errs, ad, adc = [], app.get("ad") or {}, d.get("ad") or {}
    for k in ("sourceName", "entitlementExport"):
        if not ad.get(k):
            errs.append(f"ad.{k} missing")
    check_text(errs, app, "application", "application (ISC app name)")
    tmpl = ad.get("accessProfileNameTemplate", adc.get("accessProfileNameTemplate", "{app} - {name}"))
    try:
        if "{name}" not in tmpl:
            errs.append("ad.accessProfileNameTemplate must contain {name}")
        tmpl.format(app="a", name="b")
    except (KeyError, IndexError, ValueError, AttributeError, TypeError) as e:
        errs.append(f"ad.accessProfileNameTemplate invalid: {e!r}")
    try:
        re.compile(ad.get("scopeRegex") or "")
    except (re.error, TypeError) as e:
        errs.append(f"ad.scopeRegex invalid: {e}")
    exp = ad.get("entitlementExport")
    if not exp:
        return errs
    cols, rows, file_errs = read_csv_checked(exp, ad.get("delimiter", ","), "entitlement export")
    vcol = ad.get("valueColumn", adc.get("valueColumn"))
    acol = ad.get("approvalAttribute", adc.get("approvalAttribute"))
    if file_errs:
        return errs + file_errs
    errs += [f"column '{c}' not in {exp} (has {cols})" for c in (vcol, acol) if c not in cols]
    if not rows:
        errs.append(f"{exp} has no rows")
    if vcol in cols and acol in cols:
        for i, r in enumerate(rows, start=2):
            try:
                if not (r.get(vcol) or "").strip():
                    raise ValueError(f"empty {vcol}")
                parse_approvers(r.get(acol))
            except ValueError as e:
                errs.append(f"{exp} line {i} ({(r.get(vcol) or '')[:60]}): {e}")
    return errs


def validate_app(app, d):
    if not isinstance(app, dict):
        return [f"app entry must be a mapping, got {type(app).__name__}"]
    errs, t = [], app.get("connectorType")
    name = app["appName"] if isinstance(app.get("appName"), str) and app["appName"] else "<missing appName>"
    check_text(errs, app, "appName")
    check_text(errs, app, "owner", "owner (alias or email)")
    if not isinstance(t, str) or t not in VALID_TYPES:
        errs.append(f"connectorType must be one of {sorted(VALID_TYPES)}")

    if t in ("sqlloader", "delimited"):
        check_text(errs, app, "sourceName", required=False)
        check_text(errs, app, "application", required=False)
        errs += access_profile_problems(app.get("accessProfiles"))

    if t == "sqlloader":
        tpl_name = app.get("template", "default")
        tpl = (d.get("sqlLoaderTemplates") or {}).get(tpl_name)
        if not tpl:
            errs.append(f"sqlLoaderTemplates.{tpl_name} not found in defaults")
        else:
            tpl = deep_merge(tpl, app.get("sqlloader"))
            if not isinstance(tpl.get("connector"), str) or not tpl["connector"] or tpl["connector"].startswith("REPLACE"):
                errs.append("SQL Loader connector script name not set in defaults")
            for k in ("identityAttribute", "displayAttribute", "attributes"):
                if not (tpl.get("schema") or {}).get(k):
                    errs.append(f"sqlloader schema.{k} missing")
            if not tpl.get("correlation"):
                errs.append("sqlloader correlation missing")

    elif t == "delimited":
        if not (d.get("connectors") or {}).get("delimited"):
            errs.append("defaults connectors.delimited not set")
        errs += delimited_problems(app.get("delimited") or {}, d)

    elif t == "ad":
        errs += ad_problems(app, d)
    return [f"[{name}] {e}" for e in errs]


def duplicates(label, names):
    groups = {}
    for n in names:
        if isinstance(n, str) and n.strip():
            groups.setdefault(norm(n), []).append(n)
    return [f"duplicate {label}: {v[0]}" for v in groups.values() if len(v) > 1]


def batch_errors(apps, defaults):
    """Everything wrong with a batch before any API call: per-app problems and name clashes
    (ISC names are unique case-insensitively; apps, applications and sources each have their own namespace)."""
    maps = [a for a in apps if isinstance(a, dict)]
    sources = [a.get("sourceName", a.get("appName")) for a in maps if a.get("connectorType") != "ad"]
    errors = (duplicates("appName", [a.get("appName") for a in maps])
              + duplicates("application", [a.get("application") for a in maps])
              + duplicates("source name", sources))
    for a in apps:
        errors += validate_app(a, defaults)
    return errors


# ---------------------------------------------------------------- onboarder
class Onboarder:
    def __init__(self, isc, defaults, runlog=None):
        self.isc = isc
        self.d = defaults
        self.runlog = runlog or RunLog(None)  # disabled when not given
        self._identity, self._approver, self._ents = {}, {}, {}

    # ----- run log
    def rec(self, event, message, level="INFO", **fields):
        self.runlog.event(event, level, message=message, **fields)

    def note(self, event, msg, *args, level=logging.INFO, **fields):
        """Console log line plus the same text as a run-log event."""
        log.log(level, msg, *args)
        self.rec(event, (msg % args).strip(), logging.getLevelName(level), **fields)

    # ----- lookups
    def find_one(self, path, name, retries=10):
        res = self.isc.request("GET", path, params={"filters": f'name eq "{esc(name)}"', "limit": 2},
                               retries=retries)
        return res[0] if res else None

    def created(self, res, what):
        """Result of a create call. None is only legitimate when dry-run swallowed the write."""
        if res is None and self.isc.dry_run:
            return None
        if not isinstance(res, dict) or not res.get("id"):
            raise ISCError(f"{what}: unexpected response {str(res)[:200]}")
        return res

    def identity_ref(self, key):
        if key not in self._identity:
            field = "email" if "@" in key else "alias"
            res = self.isc.request("GET", "/v3/public-identities",
                                   params={"filters": f'{field} eq "{esc(key)}"', "limit": 2}) or []
            if len(res) != 1:
                raise ISCError(f"Owner '{key}' resolved to {len(res)} identities (expected 1)")
            self._identity[key] = {"type": "IDENTITY", "id": res[0]["id"], "name": res[0].get("name")}
        return self._identity[key]

    def approver_identity_id(self, email):
        """Exact lookup by email. Returns None when 0 or >1 identities match."""
        if email not in self._approver:
            res = self.isc.request("GET", "/v3/public-identities",
                                   params={"filters": f'email eq "{esc(email)}"', "limit": 2}) or []
            self._approver[email] = res[0]["id"] if len(res) == 1 else None
            if len(res) > 1:
                self.note("approver_ambiguous", "  approver %s matches %d identities", email, len(res),
                          level=logging.WARNING, step="approvers")
        return self._approver[email]

    def ensure_governance_group(self, name, owner, member_ids, sync_remove=False):
        """Create (or reuse) a governance group named after the access profile and sync members."""
        gg = self.find_one("/beta/workgroups", name)
        state = "existing"
        if not gg:
            gg = self.created(self.isc.request("POST", "/beta/workgroups", write=True, json_body={
                "name": name,
                "description": f"Level 2 approvers for access profile '{name}' (managed by onboarding pipeline)",
                "owner": {"type": "IDENTITY", "id": owner["id"]}}), f"create governance group '{name}'")
            state = "created"
            if gg is None:  # dry-run
                self.note("governance_group_created", "  [DRY-RUN] governance group '%s' with %d member(s)",
                          name, len(member_ids), step="governance_group")
                return f"DRYRUN-GG-{name}", state
        gg_id = gg["id"]
        current = set()
        if state == "existing":
            current = {m["id"] for m in self.isc.get_all(f"/beta/workgroups/{gg_id}/members")}
        to_add = [i for i in member_ids if i not in current]
        to_remove = [i for i in current if i not in set(member_ids)] if sync_remove else []
        # VERIFY in sandbox: bulk-add / bulk-delete body is a list of identity references
        if to_add:
            self.isc.request("POST", f"/beta/workgroups/{gg_id}/members/bulk-add", write=True,
                             json_body=[{"type": "IDENTITY", "id": i} for i in to_add])
        if to_remove:
            self.isc.request("POST", f"/beta/workgroups/{gg_id}/members/bulk-delete", write=True,
                             json_body=[{"type": "IDENTITY", "id": i} for i in to_remove])
        self.note("governance_group_" + ("created" if state == "created" else "reused"),
                  "  governance group '%s' [%s]: +%d / -%d member(s)", name, state, len(to_add),
                  len(to_remove), step="governance_group", added=len(to_add), removed=len(to_remove))
        return gg_id, state

    def source_entitlements(self, source_id, refresh=False):
        if refresh or source_id not in self._ents:
            self._ents[source_id] = self.isc.get_all(
                "/beta/entitlements", params={"filters": f'source.id eq "{source_id}"'})
        return self._ents[source_id]

    def wait_task(self, task_id):
        if not task_id:
            return
        timeout = time.time() + 60 * int(self.d.get("aggregationTimeoutMinutes", 30))
        while time.time() < timeout:
            st = self.isc.request("GET", f"/beta/task-status/{task_id}") or {}
            if st.get("completed"):
                status = st.get("completionStatus")
                self.note("aggregation_done", "  aggregation task %s finished: %s", task_id, status,
                          step="aggregate", task_status=status)
                if status in ("ERROR", "TERMINATED", "TEMP_ERROR"):
                    raise ISCError(f"Aggregation failed: {json.dumps(st.get('messages'))[:500]}")
                return
            time.sleep(15)
        raise ISCError(f"Aggregation task {task_id} timed out")

    # ----- source build steps
    def ensure_source(self, app, connector, connector_attrs, cluster_id=None):
        name = app.get("sourceName", app["appName"])
        owner = self.identity_ref(app["owner"])  # fail before touching anything
        existing = self.find_one("/v3/sources", name)
        if existing:
            if existing.get("connector") not in (None, connector):
                raise ISCError(f"Source '{name}' already exists with connector '{existing.get('connector')}' "
                               f"(expected '{connector}')")
            self.note("source_reused", "  source '%s' exists (%s) - reusing", name, existing["id"],
                      step="source", name=name, id=existing["id"])
            return existing, False
        body = {
            "name": name,
            "description": text_or(app.get("description"), f"Onboarded via pipeline: {app['appName']}"),
            "owner": owner,
            "connector": connector,
            "connectorAttributes": connector_attrs or {},
            "deleteThreshold": self.d.get("deleteThreshold", 10),
            "authoritative": False,
        }
        if cluster_id:
            body["cluster"] = {"type": "CLUSTER", "id": cluster_id}
        res = self.created(self.isc.request("POST", "/v3/sources", json_body=body, write=True),
                           f"create source '{name}'")
        if res is None:
            self.rec("source_created", f"would create source '{name}'", step="source", name=name)
            return {"id": f"DRYRUN-{name}", "name": name, "_dry": True}, True
        self.note("source_created", "  created source '%s' (%s)", name, res["id"],
                  step="source", name=name, id=res["id"])
        return res, True

    def apply_schema(self, source_id, sch, columns):
        schemas = self.isc.request("GET", f"/v3/sources/{source_id}/schemas") or []
        log.info("  schemas on source: %s", [(s.get("name"), s.get("nativeObjectType")) for s in schemas])
        acct = next((s for s in schemas if (s.get("nativeObjectType") or s.get("name", "")).lower() in ("account", "user")), None)
        if not acct and len(schemas) == 1:
            acct = schemas[0]
        if not acct:
            raise ISCError(f"Account schema not found on source (found: {[s.get('name') for s in schemas]})")
        ent_cols = set(sch.get("entitlementColumns") or [])
        multi = set(sch.get("multiValued", list(ent_cols)))
        needed = {sch["identityAttribute"], sch["displayAttribute"]} | ent_cols
        missing = needed - set(columns)
        if missing:
            raise ISCError(f"Schema columns not present in attributes/header: {sorted(missing)}")
        acct["attributes"] = [{
            "name": c, "type": "STRING", "description": "",
            "isMulti": c in multi, "isEntitlement": c in ent_cols, "isGroup": False,
        } for c in columns]
        acct["identityAttribute"] = sch["identityAttribute"]
        acct["displayAttribute"] = sch["displayAttribute"]
        self.isc.request("PUT", f"/v3/sources/{source_id}/schemas/{acct['id']}", json_body=acct, write=True)
        self.note("schema_applied", "  schema applied (%d attributes, entitlements: %s)",
                  len(columns), sorted(ent_cols), step="schema", source_id=source_id)

    def apply_correlation(self, source_id, rules):
        # VERIFY in sandbox: payload shape of the correlation-config API for your tenant version.
        cfg = self.isc.request("GET", f"/beta/sources/{source_id}/correlation-config") or {}
        cfg["attributeAssignments"] = [{
            "property": r["identityAttr"], "value": r["accountAttr"], "operation": "EQ",
            "complex": False, "ignoreCase": r.get("ignoreCase", True),
            "matchMode": "ANYWHERE", "filterString": "",
        } for r in rules]
        self.isc.request("PUT", f"/beta/sources/{source_id}/correlation-config", json_body=cfg, write=True)
        self.note("correlation_applied", "  correlation applied: %s",
                  [(r["identityAttr"], r["accountAttr"]) for r in rules],
                  step="correlation", source_id=source_id)

    def aggregate(self, source_id, file_path=None):
        fh = open(file_path, "rb") if file_path else None
        try:
            files = {"file": (os.path.basename(file_path), fh, "text/csv")} if fh else None
            # /v3/ returned 404 for load-accounts on partner7884, so this one call uses /v2024/
            res = self.isc.request("POST", f"/v2024/sources/{source_id}/load-accounts",
                                   files=files, data={"disableOptimization": "true"}, write=True)
        finally:
            if fh:
                fh.close()
        if res:
            self.wait_task((res.get("task") or {}).get("id"))

    # ----- access build steps
    def make_requestable(self, ents):
        to_flag = [e["id"] for e in ents if not e.get("requestable")]
        for batch in chunks(to_flag, 50):  # bulk-update max 50 ids per call
            self.isc.request("POST", "/beta/entitlements/bulk-update", write=True, json_body={
                "entitlementIds": batch,
                "jsonPatch": [{"op": "replace", "path": "/requestable", "value": True}]})
        cfg = {"accessRequestConfig": {
            "approvalSchemes": NON_AD_APPROVALS,
            "requestCommentRequired": self.d.get("requestCommentRequired", True),
            "denialCommentRequired": False}}
        for e in ents:
            self.isc.request("PUT", f"/beta/entitlements/{e['id']}/entitlement-request-config",
                             json_body=cfg, write=True)
        self.note("entitlements_requestable",
                  "  %d entitlements requestable (%d newly flagged), approvals: Manager -> Source Owner",
                  len(ents), len(to_flag), step="requestable", count=len(ents), newly_flagged=len(to_flag))

    def ensure_access_profile(self, name, desc, owner, source, ent_ids, schemes):
        req_cfg = {"commentsRequired": self.d.get("requestCommentRequired", True),
                   "denialCommentsRequired": False, "approvalSchemes": schemes}
        try:
            # short retry here: if the lookup is unavailable we fall back to creating directly
            existing = self.find_one("/v3/access-profiles", name, retries=4)
        except ISCError as e:
            self.note("access_profile_lookup_failed",
                      "  access profile lookup unavailable (%s) - trying to create directly", str(e)[:150],
                      level=logging.WARNING, step="access_profile")
            existing = None
        if existing:
            on = (existing.get("source") or {}).get("id")
            if on and on != source["id"]:
                raise ISCError(f"Access profile '{name}' already exists on another source ({on})")
            if self.d.get("updateExistingAccessProfiles"):
                self.isc.patch(f"/v3/access-profiles/{existing['id']}", [
                    {"op": "replace", "path": "/entitlements",
                     "value": [{"type": "ENTITLEMENT", "id": i} for i in ent_ids]},
                    {"op": "replace", "path": "/accessRequestConfig", "value": req_cfg}])
                self.note("access_profile_updated", "  updated access profile '%s'", name,
                          step="access_profile", id=existing["id"])
            else:
                self.rec("access_profile_reused", f"access profile '{name}' exists - reusing",
                         step="access_profile", id=existing["id"])
            return existing["id"], "existing"
        body = {
            "name": name, "description": desc, "owner": owner,
            "source": {"type": "SOURCE", "id": source["id"], "name": source["name"]},
            "entitlements": [{"type": "ENTITLEMENT", "id": i} for i in ent_ids],
            "enabled": True, "requestable": True, "accessRequestConfig": req_cfg,
        }
        res = self.created(self.isc.request("POST", "/v3/access-profiles", json_body=body, write=True),
                           f"create access profile '{name}'")
        ap_id = res["id"] if res else f"DRYRUN-{name}"
        self.rec("access_profile_created", f"access profile '{name}' created", step="access_profile", id=ap_id)
        return ap_id, "created"

    def ensure_source_app(self, app_name, desc, owner, source, ap_ids):
        desc = text_or(desc, f"{app_name} - created by the ISC onboarding pipeline")
        real_ids = [i for i in ap_ids if not i.startswith("DRYRUN")]
        existing = self.find_one("/beta/source-apps/all", app_name)
        if existing:
            on = (existing.get("accountSource") or {}).get("id")
            if on and on != source["id"]:
                raise ISCError(f"Application '{app_name}' already exists for another source ({on})")
            app_id = existing["id"]
            self.rec("application_reused", f"application '{app_name}' exists - reusing",
                     step="application", id=app_id)
            current = [a["id"] for a in self.isc.get_all(f"/beta/source-apps/{app_id}/access-profiles")]
        else:
            res = self.created(self.isc.request("POST", "/beta/source-apps", write=True, json_body={
                "name": app_name, "description": desc, "matchAllAccounts": False,
                "accountSource": {"type": "SOURCE", "id": source["id"], "name": source["name"]}}),
                f"create application '{app_name}'")
            if res is None:
                self.note("application_created", "  [DRY-RUN] would create application '%s' with %d access profiles",
                          app_name, len(ap_ids), step="application")
                return
            app_id, current = res["id"], []
            self.rec("application_created", f"application '{app_name}' created", step="application", id=app_id)
        merged = list(dict.fromkeys(current + real_ids))
        ops = [{"op": "replace", "path": "/owner", "value": {"type": "IDENTITY", "id": owner["id"]}},
               {"op": "replace", "path": "/enabled", "value": True}]
        if merged:
            ops.insert(0, {"op": "replace", "path": "/accessProfiles", "value": merged})
        self.isc.patch(f"/beta/source-apps/{app_id}", ops)
        self.note("access_profile_attached", "  application '%s': %d access profiles attached",
                  app_name, len(merged), step="application", count=len(merged))

    # ----- handlers
    def _finish_non_ad(self, app, source, result):
        ents = self.source_entitlements(source["id"], refresh=True)
        if not ents:
            self.note("no_entitlements", "  no entitlements found on source after aggregation",
                      level=logging.WARNING, step="requestable")
        self.make_requestable(ents)
        result["entitlementsRequestable"] = len(ents)
        owner = self.identity_ref(app["owner"])

        # 1. Application first - it does not depend on access profiles
        if app.get("application"):
            self.ensure_source_app(app["application"], app.get("description", ""),
                                   owner, source, [])
            result["application"] = app["application"]

        # 2. Access profiles afterwards - a failure here keeps the application
        by_value = {e["value"].lower(): e["id"] for e in ents}
        ap_ids, problems = [], []
        try:
            for ap in app.get("accessProfiles") or []:
                wanted = list(dict.fromkeys(v.lower() for v in ap["entitlementValues"]))
                missing = [v for v in wanted if v not in by_value]
                if missing:
                    self.note("entitlements_missing", "  AP '%s' skipped: entitlement values not found: %s",
                              ap["name"], missing, level=logging.ERROR, step="access_profile")
                    problems.append(f"{ap['name']}: entitlement values not found {missing}")
                    continue
                ap_id, _ = self.ensure_access_profile(ap["name"], text_or(ap.get("description"), ap["name"]),
                                                      owner, source, [by_value[v] for v in wanted], NON_AD_APPROVALS)
                ap_ids.append(ap_id)
            if app.get("application") and ap_ids:
                self.ensure_source_app(app["application"], app.get("description", ""),
                                       owner, source, ap_ids)
        except ISCError as e:
            self.note("access_profiles_failed", "  access profiles not created (application kept): %s", e,
                      level=logging.ERROR, step="access_profile")
            problems.append(str(e))
        if problems:
            result.update(status="partial", accessProfileError="; ".join(problems)[:500])

    def handle_sqlloader(self, app, result):
        tpl = deep_merge(self.d["sqlLoaderTemplates"][app.get("template", "default")], app.get("sqlloader"))
        source, created = self.ensure_source(app, tpl["connector"], tpl.get("connectorAttributes"),
                                             tpl.get("clusterId") or self.d.get("clusterId"))
        result["source"] = "created" if created else "existing"
        if source.get("_dry"):
            result["note"] = "dry-run: schema/correlation/aggregation planned on new source"
            return
        if created or app.get("reapplyConfig"):
            self.apply_schema(source["id"], tpl["schema"], tpl["schema"]["attributes"])
            self.apply_correlation(source["id"], tpl["correlation"])
        self.aggregate(source["id"])
        self._finish_non_ad(app, source, result)

    def handle_delimited(self, app, result):
        dl = app["delimited"]
        attrs = deep_merge(self.d.get("delimitedConnectorAttributes"), dl.get("connectorAttributes"))
        header, _ = read_csv(dl["accountFile"], attrs.get("delimiter", ","))
        columns = dl["schema"].get("attributes") or header
        source, created = self.ensure_source(app, self.d["connectors"]["delimited"], attrs)
        result["source"] = "created" if created else "existing"
        if source.get("_dry"):
            result["note"] = f"dry-run: schema from {len(columns)} columns + correlation + upload planned"
            return
        if created or app.get("reapplyConfig"):
            self.apply_schema(source["id"], dl["schema"], columns)
            self.apply_correlation(source["id"], dl["correlation"])
        self.aggregate(source["id"], dl["accountFile"])
        self._finish_non_ad(app, source, result)

    def handle_ad(self, app, result):
        """AD: no source creation. Per in-scope group -> governance group (approvers from the
        extension attribute) -> access profile (L1 Manager, L2 governance group) -> application."""
        ad, adc = app["ad"], self.d.get("ad") or {}
        source = self.find_one("/v3/sources", ad["sourceName"])
        if not source:
            raise ISCError(f"AD source not found: {ad['sourceName']}")
        vcol = ad.get("valueColumn", adc.get("valueColumn"))
        ncol = ad.get("nameColumn", adc.get("nameColumn"))
        acol = ad.get("approvalAttribute", adc.get("approvalAttribute"))
        tmpl = ad.get("accessProfileNameTemplate", adc.get("accessProfileNameTemplate", "{app} - {name}"))
        max_len = adc.get("governanceGroupNameMaxLength", 128)
        sync_remove = adc.get("syncRemoveGovernanceGroupMembers", False)
        scope = ad.get("scopeRegex")  # optional extra filter on the value column (e.g. OU)

        # Governance group owner: configured identity (e.g. IAM service identity) or the app owner
        owner = self.identity_ref(app["owner"])
        gg_owner_key = ad.get("governanceGroupOwner") or adc.get("governanceGroupOwner")
        gg_owner = self.identity_ref(gg_owner_key) if gg_owner_key else owner

        _, rows = read_csv(ad["entitlementExport"], ad.get("delimiter", ","))
        if scope:
            rows = [r for r in rows if re.search(scope, r[vcol], re.IGNORECASE)]
        ents = {e["value"].lower(): e for e in self.source_entitlements(source["id"])}

        ap_ids, created, existing, missing, skipped, claimed = [], 0, 0, [], [], {}
        for r in rows:
            ent = ents.get(r[vcol].strip().lower())
            if not ent:
                missing.append(r[vcol])
                continue
            ap_name = tmpl.format(app=app["appName"], name=(r.get(ncol) or ent["name"]).strip())
            gg_name = gov_group_name(ap_name, max_len)
            keys = [("ap", norm(ap_name)), ("gg", norm(gg_name))]
            if any(claimed.get(k, ent["id"]) != ent["id"] for k in keys):
                log.error("  SKIP AP '%s': name collides with another group's access profile/governance group", ap_name)
                skipped.append({"accessProfile": ap_name, "reason": "name collision"})
                continue
            claimed.update(dict.fromkeys(keys, ent["id"]))

            # 1. approvers from extension attribute -> ISC identities (all must resolve)
            emails = parse_approvers(r.get(acol))
            resolved = {e: self.approver_identity_id(e) for e in emails}
            unresolved = [e for e, i in resolved.items() if not i]
            if unresolved:
                self.note("access_profile_skipped", "  SKIP AP '%s': approver(s) not found / ambiguous in ISC: %s",
                          ap_name, unresolved, level=logging.ERROR, step="approvers")
                skipped.append({"accessProfile": ap_name, "unresolvedApprovers": unresolved})
                continue

            # 2. governance group named after the access profile, members = approvers
            gg_id, _ = self.ensure_governance_group(gg_name, gg_owner,
                                                    list(resolved.values()), sync_remove)

            # 3. access profile: L1 Manager, L2 governance group
            schemes = [{"approverType": "MANAGER", "approverId": None},
                       {"approverType": "GOVERNANCE_GROUP", "approverId": gg_id}]
            desc = text_or(r.get("description"), f"{app['appName']} access via AD group {ent['name']}")
            ap_id, state = self.ensure_access_profile(ap_name, desc, owner, source, [ent["id"]], schemes)
            ap_ids.append(ap_id)
            created += state == "created"
            existing += state == "existing"
            log.info("  AP '%s' [%s] approvals: Manager -> GG '%s' (%d approver(s))",
                     ap_name, state, ap_name, len(emails))

        if missing:
            self.note("export_rows_missing", "  %d export rows not found in ISC (aggregate AD first?): %s",
                      len(missing), missing[:5], level=logging.WARNING, step="entitlements")
        if ap_ids:
            self.ensure_source_app(app["application"], app.get("description", ""), owner, source, ap_ids)
        result.update(accessProfilesCreated=created, accessProfilesExisting=existing,
                      exportRowsNotFound=len(missing), skipped=skipped)
        if skipped or missing:
            result["status"] = "partial"

    def run(self, app):
        result = {"app": app["appName"], "type": app["connectorType"], "status": "ok"}
        self.runlog.app = app["appName"]
        self.note("app_start", "=== %s (%s)", app["appName"], app["connectorType"], type=app["connectorType"])
        try:
            getattr(self, f"handle_{app['connectorType']}")(app, result)
        except Exception as e:  # keep going with the rest of the batch
            log.exception("  FAILED: %s", e)
            result.update(status="failed", error=str(e)[:500])
            self.runlog.failure("app_failed", e)
        else:
            self.rec("app_done", f"{app['appName']} finished: {result['status']}", outcome=result["status"])
        return result


# ---------------------------------------------------------------- main
def write_summary(results, dry_run, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    md = os.environ.get("GITHUB_STEP_SUMMARY")
    if md:
        with open(md, "a", encoding="utf-8") as f:
            f.write(f"## ISC onboarding {'(DRY-RUN)' if dry_run else ''}\n\n"
                    "| App | Type | Status | Details |\n|---|---|---|---|\n")
            for r in results:
                details = {k: v for k, v in r.items() if k not in ("app", "type", "status")}
                f.write(f"| {r['app']} | {r['type']} | {r['status']} | {json.dumps(details)} |\n")


def file_apps(path):
    """A file can hold one app or a bulk 'apps:' list."""
    try:
        cfg = load_yaml(path)
    except yaml.YAMLError as e:
        sys.exit(f"{path}: invalid YAML: {e}")
    if not isinstance(cfg, dict):
        sys.exit(f"{path}: expected a mapping, got {type(cfg).__name__}")
    if "apps" not in cfg:
        return [cfg]
    if not isinstance(cfg["apps"], list):
        sys.exit(f"{path}: 'apps' must be a list")
    return cfg["apps"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apps", default="onboarding/apps/*.yaml")
    p.add_argument("--defaults", default="onboarding/config/defaults.yaml")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--report", default="onboarding-report.json")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    defaults = load_yaml(args.defaults)
    apps = []
    for f in sorted(glob.glob(args.apps)):
        apps.extend(file_apps(f))
    if not apps:
        sys.exit(f"No app configs matched {args.apps}")

    errors = batch_errors(apps, defaults)
    if errors:
        print("Validation failed:\n  " + "\n  ".join(errors))
        sys.exit(1)
    log.info("Validated %d app(s)", len(apps))
    if args.validate_only:
        return

    runlog = RunLog(os.environ.get("ISC_LOG_DIR") or "onboarding/logs",
                    target=os.environ.get("ISC_TARGET"), dry_run=args.dry_run)
    results, crashed = [], False
    try:
        isc = ISCClient(os.environ["ISC_TENANT"], os.environ["ISC_CLIENT_ID"],
                        os.environ["ISC_CLIENT_SECRET"], dry_run=args.dry_run,
                        base_url=os.environ.get("ISC_BASE_URL") or None, on_event=runlog.event)
        ob = Onboarder(isc, defaults, runlog)
        results = [ob.run(a) for a in apps]
        write_summary(results, args.dry_run, args.report)
    except BaseException as e:  # also Ctrl-C / cancellation; re-raised after logging
        crashed = True
        runlog.failure("run_crashed", e)
        raise
    finally:
        runlog.end(results, crashed)
    failed = [r["app"] for r in results if r["status"] != "ok"]  # failed or partial
    log.info("Done: %d ok, %d failed %s", len(results) - len(failed), len(failed), failed or "")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
