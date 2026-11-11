"""JDBC connector: create a JDBC source on a virtual appliance cluster from a template, apply schema and
correlation, aggregate (the appliance reads the database - there is no feed file), then make entitlements
requestable (L1 Manager -> L2 Source Owner).

Database credentials never go in YAML: list them under `secretEnv` as {connector attribute: ENV VAR NAME} and
the value is read from the environment at run time, only when the source has to be created."""
import os
import re

from common import deep_merge
from connectors import Connector, register
from isc_client import ISCError
from run_log import register_secret
from validation import source_app_problems

SECRET_KEY = re.compile(r"pass|secret|token|credential|private", re.I)
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


@register
class Jdbc(Connector):
    type = "jdbc"

    @staticmethod
    def template(defaults, app):
        name = app.get("template", "default")
        base = (defaults.get("jdbcTemplates") or {}).get(name)
        return name, (deep_merge(base, app.get("jdbc")) if base else None)

    def validate(self, app, defaults):
        errs = source_app_problems(app)
        name, tpl = self.template(defaults, app)
        if tpl is None:
            return errs + [f"jdbcTemplates.{name} not found in defaults"]
        if not isinstance(tpl.get("connector"), str) or not tpl["connector"] or tpl["connector"].startswith("REPLACE"):
            errs.append("JDBC connector script name not set in defaults (jdbcTemplates.<name>.connector)")
        if not (tpl.get("clusterId") or defaults.get("clusterId")):
            errs.append("jdbc clusterId not set: JDBC sources run on a virtual appliance cluster")
        attrs, secrets = tpl.get("connectorAttributes") or {}, tpl.get("secretEnv") or {}
        if not isinstance(attrs, dict):
            return errs + ["jdbc connectorAttributes must be a mapping"]
        if not isinstance(secrets, dict):
            return errs + ["jdbc secretEnv must be a mapping of attribute -> environment variable"]
        for attr, env in secrets.items():
            if not isinstance(env, str) or not ENV_NAME.match(env):
                errs.append(f"jdbc secretEnv.{attr} must name an environment variable like JDBC_PASSWORD")
        for key, value in attrs.items():
            if SECRET_KEY.search(str(key)) and value not in (None, ""):
                errs.append(f"jdbc connectorAttributes.{key} looks like a credential: use secretEnv, not YAML")
        url = attrs.get("url")
        if url is not None and not str(url).startswith("jdbc:"):
            errs.append("jdbc connectorAttributes.url must start with 'jdbc:'")
        sch = tpl.get("schema") or {}
        for k in ("identityAttribute", "displayAttribute", "attributes"):
            if not sch.get(k):
                errs.append(f"jdbc schema.{k} missing")
        if not tpl.get("correlation"):
            errs.append("jdbc correlation missing (needs human sign-off)")
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
