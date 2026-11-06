"""Onboarder: the ISC operations shared by all connectors (lookups, sources, schemas, access profiles,
applications, governance groups) and the per-app runner that dispatches to the connector registry."""
import json
import logging
import os
import time

import connectors
from common import chunks, esc, text_or
from isc_client import ISCError
from run_log import RunLog
from uploader import upload_with_utility

log = logging.getLogger("onboard")

# Non-AD requirement: Level 1 Manager, Level 2 Source Owner
NON_AD_APPROVALS = [
    {"approverType": "MANAGER", "approverId": None},
    {"approverType": "SOURCE_OWNER", "approverId": None},
]


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
        jar = os.environ.get("ISC_UPLOAD_JAR", "").strip()
        if jar and file_path:
            return self.aggregate_with_utility(jar, source_id, file_path)
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

    def aggregate_with_utility(self, jar, source_id, file_path):
        """Feed upload through SailPoint's File Upload Utility JAR, then wait for the accounts to appear."""
        if self.isc.dry_run:
            self.note("upload_dry_run", "  [DRY-RUN] would upload %s with the File Upload Utility", file_path, step="aggregate")
            return
        out = upload_with_utility(jar, self.isc.base, self.isc.client_id, self.isc.client_secret, source_id, file_path)
        self.note("upload_utility_done", "  File Upload Utility finished: %s", out[-300:], step="aggregate")
        timeout = time.time() + 60 * int(self.d.get("aggregationTimeoutMinutes", 30))
        while time.time() < timeout:
            if self.isc.request("GET", "/v3/accounts", params={"filters": f'sourceId eq "{source_id}"', "limit": 1}):
                time.sleep(int(self.d.get("postAggregationPauseSeconds", 20)))  # let entitlements settle
                return
            time.sleep(15)
        raise ISCError("no accounts appeared on the source after the File Upload Utility finished")

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

    # ----- shared tail of the source-based connectors (sqlloader, delimited)
    def finish_non_ad(self, app, source, result):
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

    def run(self, app):
        result = {"app": app["appName"], "type": app["connectorType"], "status": "ok"}
        self.runlog.app = app["appName"]
        self.note("app_start", "=== %s (%s)", app["appName"], app["connectorType"], type=app["connectorType"])
        try:
            connectors.get(app["connectorType"]).handle(self, app, result)
        except Exception as e:  # keep going with the rest of the batch
            log.exception("  FAILED: %s", e)
            result.update(status="failed", error=str(e)[:500])
            self.runlog.failure("app_failed", e)
        else:
            self.rec("app_done", f"{app['appName']} finished: {result['status']}", outcome=result["status"])
        return result
