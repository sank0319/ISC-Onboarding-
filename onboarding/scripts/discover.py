#!/usr/bin/env python3
"""Read-only look at the tenant: find a connector's settings to copy into onboarding/config/defaults.yaml.

  discover.py connectors [--query sql]       connector types whose name/type/script name contains the text
  discover.py sources    [--query sql]       existing sources whose connector contains the text
  discover.py source --query "Source name"   one source: connector, cluster, connectorAttributes (secrets
                                             masked), account schema, and a defaults.yaml template block

Only GET requests are made. Values whose key looks like a credential, and anything that looks like a token,
password or key, are masked before printing; review the output before pasting it anywhere.
"""
import argparse
import re
import sys

import yaml

from isc_client import ISCClient, ISCError
from run_log import redact, register_secret
from secrets_store import SecretsError, load_credentials

SECRET_KEY = re.compile(r"pass|secret|token|key|credential|private|cert|auth", re.I)
MASK = "<REDACTED>"
URL_USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")


def mask(value, key=""):
    """Copy of a JSON-like value with credential-looking data masked."""
    if isinstance(value, dict):
        return {k: mask(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, key) for v in value]
    if value in (None, "") or isinstance(value, (bool, int, float)):
        return value
    if SECRET_KEY.search(key):
        return MASK
    return URL_USERINFO.sub(r"\1" + MASK + "@", redact(value))


def match(text, *fields):
    needle = (text or "").lower()
    return any(needle in str(f or "").lower() for f in fields)


def listing(isc, paths):
    """get_all on the first path that works (v3, then beta); error from the last one if none do."""
    err = None
    for path in paths:
        try:
            return isc.get_all(path), path
        except ISCError as e:
            err = e
    raise err


def show_connectors(isc, query):
    rows, path = listing(isc, ["/v3/connectors", "/beta/connectors"])
    hits = [c for c in rows if match(query, c.get("name"), c.get("type"), c.get("scriptName"))]
    print(f"{len(hits)} of {len(rows)} connector(s) match '{query}' ({path})")
    for c in sorted(hits, key=lambda c: str(c.get("scriptName"))):
        print(f"  scriptName={c.get('scriptName')}  name={c.get('name')}  type={c.get('type')}  "
              f"directConnect={c.get('directConnect')}")
    return 0 if hits else 1


def show_sources(isc, query):
    rows = isc.get_all("/v3/sources")
    hits = [s for s in rows if match(query, s.get("connector"), s.get("type"))]
    print(f"{len(hits)} of {len(rows)} source(s) use a connector matching '{query}'")
    for s in hits:
        cluster = (s.get("cluster") or {}).get("name") or (s.get("cluster") or {}).get("id")
        print(f"  name={s.get('name')}  connector={s.get('connector')}  cluster={cluster}  id={s.get('id')}")
    return 0 if hits else 1


def template(src, schema):
    """A sqlLoaderTemplates block for defaults.yaml, built from a working source."""
    attrs = schema.get("attributes") or []
    return {
        "clusterId": (src.get("cluster") or {}).get("id") or "",
        "sqlLoaderTemplates": {"default": {
            "connector": src.get("connector"),
            "connectorAttributes": mask(src.get("connectorAttributes") or {}),
            "schema": {
                "identityAttribute": schema.get("identityAttribute"),
                "displayAttribute": schema.get("displayAttribute"),
                "attributes": [a.get("name") for a in attrs],
                "entitlementColumns": [a.get("name") for a in attrs if a.get("isEntitlement")],
            },
            "correlation": [{"identityAttr": "uid", "accountAttr": "REPLACE_WITH_ACCOUNT_ATTRIBUTE"}],
        }}}


def show_source(isc, name):
    found = isc.request("GET", "/v3/sources", params={"filters": f'name eq "{name.replace(chr(34), "")}"', "limit": 2})
    if not found:
        print(f"no source named '{name}'")
        return 1
    src = found[0]
    schemas = isc.request("GET", f"/v3/sources/{src['id']}/schemas") or []
    schema = next((s for s in schemas if str(s.get("nativeObjectType") or s.get("name")).lower() in ("account", "user")),
                  schemas[0] if len(schemas) == 1 else {})
    cluster = src.get("cluster") or {}
    print(f"Source: {src.get('name')} ({src.get('id')})")
    print(f"connector: {src.get('connector')}   cluster: {cluster.get('id')} ({cluster.get('name')})")
    print(f"account schema: {schema.get('name')}  identity={schema.get('identityAttribute')}  "
          f"display={schema.get('displayAttribute')}  attributes={[a.get('name') for a in schema.get('attributes') or []]}")
    print("\n--- merge into onboarding/config/defaults.yaml (masked values must be filled in, secrets never committed) ---")
    print(yaml.safe_dump(template(src, schema), sort_keys=False, default_flow_style=False).rstrip())
    return 0


def main(argv=None, isc=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("what", choices=["connectors", "sources", "source"])
    p.add_argument("--query", default=None, help="text to look for (default: sql; required for 'source')")
    args = p.parse_args(argv)
    if args.what == "source" and not args.query:
        p.error("'source' needs --query \"<source name>\"")
    query = args.query if args.query is not None else "sql"
    try:
        if isc is None:
            creds, _ = load_credentials()
            for k in ("ISC_CLIENT_ID", "ISC_CLIENT_SECRET"):
                register_secret(creds[k])
            isc = ISCClient(creds["ISC_TENANT"], creds["ISC_CLIENT_ID"], creds["ISC_CLIENT_SECRET"],
                            base_url=creds.get("ISC_BASE_URL"))
        return {"connectors": show_connectors, "sources": show_sources, "source": show_source}[args.what](isc, query)
    except (SecretsError, ISCError) as e:
        print(f"discover failed: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
