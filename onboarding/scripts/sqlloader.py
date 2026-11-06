"""SQL Loader connector: create the source from a template (schema + correlation predefined),
upload the account feed with the load-accounts API (what the SailPoint File Upload Utility calls) and
aggregate, then make entitlements requestable (L1 Manager -> L2 Source Owner)."""
from common import deep_merge
from connectors import Connector, register
from validation import read_csv_checked, source_app_problems


@register
class SqlLoader(Connector):
    type = "sqlloader"

    def validate(self, app, defaults):
        errs = source_app_problems(app)
        tpl_name = app.get("template", "default")
        tpl = (defaults.get("sqlLoaderTemplates") or {}).get(tpl_name)
        if not tpl:
            return errs + [f"sqlLoaderTemplates.{tpl_name} not found in defaults"]
        tpl = deep_merge(tpl, app.get("sqlloader"))
        if not isinstance(tpl.get("connector"), str) or not tpl["connector"] or tpl["connector"].startswith("REPLACE"):
            errs.append("SQL Loader connector script name not set in defaults")
        for k in ("identityAttribute", "displayAttribute", "attributes"):
            if not (tpl.get("schema") or {}).get(k):
                errs.append(f"sqlloader schema.{k} missing")
        if not tpl.get("correlation"):
            errs.append("sqlloader correlation missing")
        sl = app.get("sqlloader") or {}
        if not sl.get("accountFile"):
            return errs + ["sqlloader.accountFile missing (the CSV that is uploaded to the source)"]
        delim = (tpl.get("connectorAttributes") or {}).get("delimiter", ",")
        header, rows, file_errs = read_csv_checked(sl["accountFile"], delim, "sqlloader.accountFile")
        if file_errs:
            return errs + file_errs
        if not rows:
            errs.append("accountFile has no account rows")
        sch = tpl.get("schema") or {}
        wanted = [sch.get("identityAttribute"), sch.get("displayAttribute"), *(sch.get("entitlementColumns") or [])]
        wanted += [r.get("accountAttr") for r in (tpl.get("correlation") or []) if isinstance(r, dict)]
        errs += [f"column '{c}' not in accountFile header {header}" for c in dict.fromkeys(wanted)
                 if isinstance(c, str) and c and c not in header]
        return errs

    def handle(self, ob, app, result):
        tpl = deep_merge(ob.d["sqlLoaderTemplates"][app.get("template", "default")], app.get("sqlloader"))
        source, created = ob.ensure_source(app, tpl["connector"], tpl.get("connectorAttributes"),
                                           tpl.get("clusterId") or ob.d.get("clusterId"))
        result["source"] = "created" if created else "existing"
        if source.get("_dry"):
            result["note"] = "dry-run: schema/correlation/aggregation planned on new source"
            return
        if created or app.get("reapplyConfig"):
            ob.apply_schema(source["id"], tpl["schema"], tpl["schema"]["attributes"])
            ob.apply_correlation(source["id"], tpl["correlation"])
        ob.aggregate(source["id"], app["sqlloader"]["accountFile"])
        ob.finish_non_ad(app, source, result)

    def feed(self, app):
        sl = app.get("sqlloader")
        return (sl.get("accountFile") if isinstance(sl, dict) else None), f"{app.get('sourceName', app.get('appName'))}.csv"
