"""Delimited-file connector: create source, schema from the file header, correlation, upload and
aggregate the account feed, make entitlements requestable (L1 Manager -> L2 Source Owner)."""
from common import deep_merge, read_csv
from connectors import Connector, register
from validation import read_csv_checked, source_app_problems


@register
class Delimited(Connector):
    type = "delimited"

    def validate(self, app, defaults):
        errs = source_app_problems(app)
        if not (defaults.get("connectors") or {}).get("delimited"):
            errs.append("defaults connectors.delimited not set")
        return errs + self.problems(app.get("delimited") or {}, defaults)

    @staticmethod
    def problems(dl, d):
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

    def handle(self, ob, app, result):
        dl = app["delimited"]
        attrs = deep_merge(ob.d.get("delimitedConnectorAttributes"), dl.get("connectorAttributes"))
        header, _ = read_csv(dl["accountFile"], attrs.get("delimiter", ","))
        columns = dl["schema"].get("attributes") or header
        source, created = ob.ensure_source(app, ob.d["connectors"]["delimited"], attrs)
        result["source"] = "created" if created else "existing"
        if source.get("_dry"):
            result["note"] = f"dry-run: schema from {len(columns)} columns + correlation + upload planned"
            return
        if created or app.get("reapplyConfig"):
            ob.apply_schema(source["id"], dl["schema"], columns)
            ob.apply_correlation(source["id"], dl["correlation"])
        ob.aggregate(source["id"], dl["accountFile"])
        ob.finish_non_ad(app, source, result)

    def feed(self, app):
        dl = app.get("delimited")
        return (dl.get("accountFile") if isinstance(dl, dict) else None), f"{app.get('sourceName', app.get('appName'))}.csv"
