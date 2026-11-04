"""Template for a new connector: copy to <type>.py (no leading underscore) and fill in.

The file is picked up automatically; nothing else in the code needs to change.
"""
from connectors import Connector, register
from validation import source_app_problems


@register
class Template(Connector):
    type = "template"  # the value of connectorType in app YAML files

    def validate(self, app, defaults):
        cfg = app.get(self.type) or {}
        errs = source_app_problems(app)  # drop this if the connector has no source/application/access profiles
        if not cfg.get("example"):
            errs.append(f"{self.type}.example missing")
        return errs

    def handle(self, ob, app, result):
        """Use the shared Onboarder steps (ensure_source, apply_schema, apply_correlation, aggregate,
        finish_non_ad, ...) in the order this connector needs."""
        source, created = ob.ensure_source(app, "connector-script-name", {})
        result["source"] = "created" if created else "existing"
        if source.get("_dry"):
            return
        ob.aggregate(source["id"])
        ob.finish_non_ad(app, source, result)

    def feed(self, app):  # only for connectors fed by a file; delete otherwise
        cfg = app.get(self.type)
        return (cfg.get("feedFile") if isinstance(cfg, dict) else None), f"{app.get('sourceName', app.get('appName'))}.csv"
