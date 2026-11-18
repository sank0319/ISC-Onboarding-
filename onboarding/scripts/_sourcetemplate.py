"""Base class for connectors that create a source from a template and then let ISC pull the data itself
(JDBC, API, Windows, Linux): no feed file, optional virtual appliance cluster, credentials only from the environment.

A subclass sets `type` (the connectorType in app YAML and the app's own config key), `templates_key` (the block in
the connector's template.yaml) and, where needed, `needs_cluster` / `url_prefix`. Nothing else is required.

Database/host credentials never go in YAML: list them under `secretEnv` as {connector attribute: ENV VAR NAME};
the value is read from the environment at run time, only when the source has to be created."""
import os
import re

from common import deep_merge
from connectors import Connector
from isc_client import ISCError
from run_log import register_secret
from validation import source_app_problems

SECRET_KEY = re.compile(r"pass|secret|token|credential|private", re.I)
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SourceTemplate(Connector):
    templates_key = ""       # e.g. "jdbcTemplates"
    needs_cluster = True     # source runs on a virtual appliance cluster
    url_prefix = None        # e.g. "jdbc:" - checked on connectorAttributes.url when set

    def template(self, defaults, app):
        name = app.get("template", "default")
        base = (defaults.get(self.templates_key) or {}).get(name)
        return name, (deep_merge(base, app.get(self.type)) if base else None)

    def validate(self, app, defaults):
        errs = source_app_problems(app)
        name, tpl = self.template(defaults, app)
        if tpl is None:
            return errs + [f"{self.templates_key}.{name} not found in defaults"]
        if not isinstance(tpl.get("connector"), str) or not tpl["connector"] or tpl["connector"].startswith("REPLACE"):
            errs.append(f"{self.type} connector script name not set in defaults ({self.templates_key}.<name>.connector)")
        if self.needs_cluster and not (tpl.get("clusterId") or defaults.get("clusterId")):
            errs.append(f"{self.type} clusterId not set: these sources run on a virtual appliance cluster")
        attrs, secrets = tpl.get("connectorAttributes") or {}, tpl.get("secretEnv") or {}
        if not isinstance(attrs, dict):
            return errs + [f"{self.type} connectorAttributes must be a mapping"]
        if not isinstance(secrets, dict):
            return errs + [f"{self.type} secretEnv must be a mapping of attribute -> environment variable"]
        for attr, env in secrets.items():
            if not isinstance(env, str) or not ENV_NAME.match(env):
                errs.append(f"{self.type} secretEnv.{attr} must name an environment variable like {self.type.upper()}_PASSWORD")
        for key, value in attrs.items():
            if SECRET_KEY.search(str(key)) and value not in (None, ""):
                errs.append(f"{self.type} connectorAttributes.{key} looks like a credential: use secretEnv, not YAML")
        url = attrs.get("url")
        if self.url_prefix and url is not None and not str(url).startswith(self.url_prefix):
            errs.append(f"{self.type} connectorAttributes.url must start with '{self.url_prefix}'")
        sch = tpl.get("schema") or {}
        for k in ("identityAttribute", "displayAttribute", "attributes"):
            if not sch.get(k):
                errs.append(f"{self.type} schema.{k} missing")
        if not tpl.get("correlation"):
            errs.append(f"{self.type} correlation missing (needs human sign-off)")
        return errs

    def handle(self, ob, app, result):
        _, tpl = self.template(ob.d, app)
        attrs = dict(tpl.get("connectorAttributes") or {})
        name = app.get("sourceName", app["appName"])
        if not ob.isc.dry_run and not ob.find_one("/v3/sources", name):  # secrets are only needed to create it
            for attr, env in (tpl.get("secretEnv") or {}).items():
                value = os.environ.get(env, "")
                if not value:
                    raise ISCError(f"environment variable {env} (for {attr}) is not set")
                register_secret(value)
                attrs[attr] = value
        source, created = ob.ensure_source(app, tpl["connector"], attrs, tpl.get("clusterId") or ob.d.get("clusterId"))
        result["source"] = "created" if created else "existing"
        if source.get("_dry"):
            result["note"] = "dry-run: schema/correlation/aggregation planned on new source"
            return
        if created or app.get("reapplyConfig"):
            ob.apply_schema(source["id"], tpl["schema"], tpl["schema"]["attributes"])
            ob.apply_correlation(source["id"], tpl["correlation"])
        ob.aggregate(source["id"])
        ob.finish_non_ad(app, source, result)
