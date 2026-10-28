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

log = logging.getLogger("onboard")

VALID_TYPES = {"sqlloader", "delimited", "ad"}

# Non-AD requirement: Level 1 Manager, Level 2 Source Owner
NON_AD_APPROVALS = [
    {"approverType": "MANAGER", "approverId": None},
    {"approverType": "SOURCE_OWNER", "approverId": None},
]

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
    return str(v).replace('"', '\\"')


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
def validate_app(app, d):
    errs = []
    name = app.get("appName", "<missing appName>")
    t = app.get("connectorType")
    if not app.get("appName"):
        errs.append("appName is required")
    if t not in VALID_TYPES:
        errs.append(f"connectorType must be one of {sorted(VALID_TYPES)}")
    if not app.get("owner"):
        errs.append("owner (alias or email) is required")

    if t == "sqlloader":
        tpl_name = app.get("template", "default")
        tpl = (d.get("sqlLoaderTemplates") or {}).get(tpl_name)
        if not tpl:
            errs.append(f"sqlLoaderTemplates.{tpl_name} not found in defaults")
        else:
            tpl = deep_merge(tpl, app.get("sqlloader"))
            if not tpl.get("connector") or tpl["connector"].startswith("REPLACE"):
                errs.append("SQL Loader connector script name not set in defaults")
            for k in ("identityAttribute", "displayAttribute", "attributes"):
                if not (tpl.get("schema") or {}).get(k):
                    errs.append(f"sqlloader schema.{k} missing")
            if not tpl.get("correlation"):
                errs.append("sqlloader correlation missing")

    elif t == "delimited":
        dl = app.get("delimited") or {}
        sch = dl.get("schema") or {}
        for k in ("identityAttribute", "displayAttribute", "entitlementColumns"):
            if not sch.get(k):
                errs.append(f"delimited.schema.{k} missing")
        if not dl.get("correlation"):
            errs.append("delimited.correlation missing (needs human sign-off)")
        if not dl.get("accountFile"):
            errs.append("delimited.accountFile missing")

    elif t == "ad":
        ad = app.get("ad") or {}
        adc = d.get("ad") or {}
        for k in ("sourceName", "entitlementExport"):
            if not ad.get(k):
                errs.append(f"ad.{k} missing")
        if not app.get("application"):
            errs.append("application (ISC app name) is required for AD")
        exp = ad.get("entitlementExport")
        if exp and os.path.exists(exp):
            cols, rows = read_csv(exp, ad.get("delimiter", ","))
            vcol = ad.get("valueColumn", adc.get("valueColumn"))
            acol = ad.get("approvalAttribute", adc.get("approvalAttribute"))
            for c in (vcol, acol):
                if c not in cols:
                    errs.append(f"column '{c}' not in {exp} (has {cols})")
            if acol in cols:
                for i, r in enumerate(rows, start=2):
                    try:
                        parse_approvers(r.get(acol))
                    except ValueError as e:
                        errs.append(f"{exp} line {i} ({r.get(vcol, '')[:60]}): {e}")
        elif exp:
            errs.append(f"entitlement export not found: {exp}")
    return [f"[{name}] {e}" for e in errs]


# ---------------------------------------------------------------- onboarder
class Onboarder:
    def __init__(self, isc, defaults):
        self.isc = isc
        self.d = defaults
        self._identity, self._approver, self._ents = {}, {}, {}

    # ----- lookups
    def find_one(self, path, name):
        res = self.isc.request("GET", path, params={"filters": f'name eq "{esc(name)}"', "limit": 2})
        return res[0] if res else None

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
                log.warning("  approver %s matches %d identities", email, len(res))
        return self._approver[email]

    def ensure_governance_group(self, name, owner, member_ids, sync_remove=False):
        """Create (or reuse) a governance group named after the access profile and sync members."""
        gg = self.find_one("/beta/workgroups", name)
        state = "existing"
        if not gg:
            gg = self.isc.request("POST", "/beta/workgroups", write=True, json_body={
                "name": name,
                "description": f"Level 2 approvers for access profile '{name}' (managed by onboarding pipeline)",
                "owner": {"type": "IDENTITY", "id": owner["id"]}})
            state = "created"
            if gg is None:  # dry-run
                log.info("  [DRY-RUN] governance group '%s' with %d member(s)", name, len(member_ids))
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
        log.info("  governance group '%s' [%s]: +%d / -%d member(s)", name, state, len(to_add), len(to_remove))
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
                log.info("  aggregation task %s finished: %s", task_id, status)
                if status in ("ERROR", "TERMINATED", "TEMP_ERROR"):
                    raise ISCError(f"Aggregation failed: {json.dumps(st.get('messages'))[:500]}")
                return
            time.sleep(15)
        raise ISCError(f"Aggregation task {task_id} timed out")

    # ----- source build steps
    def ensure_source(self, app, connector, connector_attrs, cluster_id=None):
        name = app.get("sourceName", app["appName"])
        existing = self.find_one("/v3/sources", name)
        if existing:
            log.info("  source '%s' exists (%s) - reusing", name, existing["id"])
            return existing, False
        body = {
            "name": name,
            "description": app.get("description", f"Onboarded via pipeline: {app['appName']}"),
            "owner": self.identity_ref(app["owner"]),
            "connector": connector,
            "connectorAttributes": connector_attrs or {},
            "deleteThreshold": self.d.get("deleteThreshold", 10),
            "authoritative": False,
        }
        if cluster_id:
            body["cluster"] = {"type": "CLUSTER", "id": cluster_id}
        res = self.isc.request("POST", "/v3/sources", json_body=body, write=True)
        if res is None:
            return {"id": f"DRYRUN-{name}", "name": name, "_dry": True}, True
        log.info("  created source '%s' (%s)", name, res["id"])
        return res, True

    def apply_schema(self, source_id, sch, columns):
        schemas = self.isc.request("GET", f"/v3/sources/{source_id}/schemas") or []
        acct = next((s for s in schemas if (s.get("nativeObjectType") or s.get("name", "")).lower() == "account"), None)
        if not acct:
            raise ISCError("Account schema not found on source")
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
        log.info("  schema applied (%d attributes, entitlements: %s)", len(columns), sorted(ent_cols))

    def apply_correlation(self, source_id, rules):
        # VERIFY in sandbox: payload shape of the correlation-config API for your tenant version.
        cfg = self.isc.request("GET", f"/beta/sources/{source_id}/correlation-config") or {}
        cfg["attributeAssignments"] = [{
            "property": r["identityAttr"], "value": r["accountAttr"], "operation": "EQ",
            "complex": False, "ignoreCase": r.get("ignoreCase", True),
            "matchMode": "ANYWHERE", "filterString": "",
        } for r in rules]
        self.isc.request("PUT", f"/beta/sources/{source_id}/correlation-config", json_body=cfg, write=True)
        log.info("  correlation applied: %s", [(r["identityAttr"], r["accountAttr"]) for r in rules])

    def aggregate(self, source_id, file_path=None):
        fh = open(file_path, "rb") if file_path else None
        try:
            files = {"file": (os.path.basename(file_path), fh, "text/csv")} if fh else None
            res = self.isc.request("POST", f"/v3/sources/{source_id}/load-accounts",
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
        log.info("  %d entitlements requestable (%d newly flagged), approvals: Manager -> Source Owner",
                 len(ents), len(to_flag))

    def ensure_access_profile(self, name, desc, owner, source, ent_ids, schemes):
        req_cfg = {"commentsRequired": self.d.get("requestCommentRequired", True),
                   "denialCommentsRequired": False, "approvalSchemes": schemes}
        existing = self.find_one("/v3/access-profiles", name)
        if existing:
            if self.d.get("updateExistingAccessProfiles"):
                self.isc.patch(f"/v3/access-profiles/{existing['id']}", [
                    {"op": "replace", "path": "/entitlements",
                     "value": [{"type": "ENTITLEMENT", "id": i} for i in ent_ids]},
                    {"op": "replace", "path": "/accessRequestConfig", "value": req_cfg}])
                log.info("  updated access profile '%s'", name)
            return existing["id"], "existing"
        body = {
            "name": name, "description": desc, "owner": owner,
            "source": {"type": "SOURCE", "id": source["id"], "name": source["name"]},
            "entitlements": [{"type": "ENTITLEMENT", "id": i} for i in ent_ids],
            "enabled": True, "requestable": True, "accessRequestConfig": req_cfg,
        }
        res = self.isc.request("POST", "/v3/access-profiles", json_body=body, write=True)
        return (res["id"] if res else f"DRYRUN-{name}"), "created"

    def ensure_source_app(self, app_name, desc, owner, source, ap_ids):
        real_ids = [i for i in ap_ids if not i.startswith("DRYRUN")]
        existing = self.find_one("/beta/source-apps", app_name)
        if existing:
            app_id = existing["id"]
            current = [a["id"] for a in self.isc.get_all(f"/beta/source-apps/{app_id}/access-profiles")]
        else:
            res = self.isc.request("POST", "/beta/source-apps", write=True, json_body={
                "name": app_name, "description": desc, "matchAllAccounts": False,
                "accountSource": {"type": "SOURCE", "id": source["id"], "name": source["name"]}})
            if res is None:
                log.info("  [DRY-RUN] would create application '%s' with %d access profiles", app_name, len(ap_ids))
                return
            app_id, current = res["id"], []
        merged = list(dict.fromkeys(current + real_ids))
        self.isc.patch(f"/beta/source-apps/{app_id}", [
            {"op": "replace", "path": "/accessProfiles", "value": merged},
            {"op": "replace", "path": "/owner", "value": {"type": "IDENTITY", "id": owner["id"]}},
            {"op": "replace", "path": "/enabled", "value": True}])
        log.info("  application '%s': %d access profiles attached", app_name, len(merged))

    # ----- handlers
    def _finish_non_ad(self, app, source, result):
        ents = self.source_entitlements(source["id"], refresh=True)
        if not ents:
            log.warning("  no entitlements found on source after aggregation")
        self.make_requestable(ents)
        result["entitlementsRequestable"] = len(ents)

        owner = self.identity_ref(app["owner"])
        by_value = {e["value"].lower(): e["id"] for e in ents}
        ap_ids = []
        for ap in app.get("accessProfiles") or []:  # optional for non-AD
            ids = [by_value[v.lower()] for v in ap["entitlementValues"] if v.lower() in by_value]
            if len(ids) != len(ap["entitlementValues"]):
                log.warning("  AP '%s': some entitlement values not found", ap["name"])
            if ids:
                ap_id, _ = self.ensure_access_profile(ap["name"], ap.get("description", ap["name"]),
                                                      owner, source, ids, NON_AD_APPROVALS)
                ap_ids.append(ap_id)
        if app.get("application") and ap_ids:
            self.ensure_source_app(app["application"], app.get("description", ""), owner, source, ap_ids)

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

        ap_ids, created, existing, missing, skipped = [], 0, 0, [], []
        for r in rows:
            ent = ents.get(r[vcol].strip().lower())
            if not ent:
                missing.append(r[vcol])
                continue
            ap_name = tmpl.format(app=app["appName"], name=(r.get(ncol) or ent["name"]).strip())

            # 1. approvers from extension attribute -> ISC identities (all must resolve)
            emails = parse_approvers(r.get(acol))
            resolved = {e: self.approver_identity_id(e) for e in emails}
            unresolved = [e for e, i in resolved.items() if not i]
            if unresolved:
                log.error("  SKIP AP '%s': approver(s) not found / ambiguous in ISC: %s", ap_name, unresolved)
                skipped.append({"accessProfile": ap_name, "unresolvedApprovers": unresolved})
                continue

            # 2. governance group named after the access profile, members = approvers
            gg_id, _ = self.ensure_governance_group(gov_group_name(ap_name, max_len), gg_owner,
                                                    list(resolved.values()), sync_remove)

            # 3. access profile: L1 Manager, L2 governance group
            schemes = [{"approverType": "MANAGER", "approverId": None},
                       {"approverType": "GOVERNANCE_GROUP", "approverId": gg_id}]
            desc = r.get("description") or f"{app['appName']} access via AD group {ent['name']}"
            ap_id, state = self.ensure_access_profile(ap_name, desc, owner, source, [ent["id"]], schemes)
            ap_ids.append(ap_id)
            created += state == "created"
            existing += state == "existing"
            log.info("  AP '%s' [%s] approvals: Manager -> GG '%s' (%d approver(s))",
                     ap_name, state, ap_name, len(emails))

        if missing:
            log.warning("  %d export rows not found in ISC (aggregate AD first?): %s",
                        len(missing), missing[:5])
        if ap_ids:
            self.ensure_source_app(app["application"], app.get("description", ""), owner, source, ap_ids)
        result.update(accessProfilesCreated=created, accessProfilesExisting=existing,
                      exportRowsNotFound=len(missing), skipped=skipped)
        if skipped:
            result["status"] = "partial"

    def run(self, app):
        result = {"app": app["appName"], "type": app["connectorType"], "status": "ok"}
        log.info("=== %s (%s)", app["appName"], app["connectorType"])
        try:
            getattr(self, f"handle_{app['connectorType']}")(app, result)
        except Exception as e:  # keep going with the rest of the batch
            log.exception("  FAILED: %s", e)
            result.update(status="failed", error=str(e)[:500])
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
        cfg = load_yaml(f)
        apps.extend(cfg.get("apps", [cfg]))  # a file can hold one app or a bulk 'apps:' list
    if not apps:
        sys.exit(f"No app configs matched {args.apps}")

    names = [a.get("appName") for a in apps]
    errors = [f"duplicate appName: {n}" for n in set(names) if names.count(n) > 1]
    for a in apps:
        errors += validate_app(a, defaults)
    if errors:
        print("Validation failed:\n  " + "\n  ".join(errors))
        sys.exit(1)
    log.info("Validated %d app(s)", len(apps))
    if args.validate_only:
        return

    isc = ISCClient(os.environ["ISC_TENANT"], os.environ["ISC_CLIENT_ID"],
                    os.environ["ISC_CLIENT_SECRET"], dry_run=args.dry_run,
                    base_url=os.environ.get("ISC_BASE_URL") or None)
    ob = Onboarder(isc, defaults)
    results = [ob.run(a) for a in apps]
    write_summary(results, args.dry_run, args.report)
    failed = [r["app"] for r in results if r["status"] != "ok"]  # failed or partial
    log.info("Done: %d ok, %d failed %s", len(results) - len(failed), len(failed), failed or "")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
