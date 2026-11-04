"""SQL Loader connector: create the source from a template (schema + correlation predefined),
aggregate, make entitlements requestable (L1 Manager -> L2 Source Owner)."""
from common import deep_merge
from connectors import Connector, register
from validation import source_app_problems


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
        ob.aggregate(source["id"])
        ob.finish_non_ad(app, source, result)
