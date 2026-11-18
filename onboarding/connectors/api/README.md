# API (web services) connector

An app with `connectorType: api` becomes a API (web services) source in ISC; ISC calls the target's REST API. There is no feed file.

Folders: `apps/` (app definitions, in `wave-N/` folders), `data/` (empty on purpose), `api-template.yaml` (this connector's
template: connector script name, settings, schema, correlation).

## Activate (needs the tenant)
1. Find the connector script name: Actions -> **ISC discover (read-only)**, what = `connectors`, query = `web`.
2. If a working API (web services) source exists, copy its settings: what = `source`, query = its exact name, template = `api`.
   Paste the printed block into `api-template.yaml`. Masked credentials become `secretEnv` entries.
3. Cluster: Usually none: ISC calls the API directly. Set `clusterId` only if the target is reachable through a virtual appliance.
4. Add the environment variable named in `secretEnv` (e.g. `API_TOKEN`) as a secret of the GitHub environment and pass it to the
   run in `.github/workflows/isc-onboarding.yml`. It is only read when a source is created.
5. Copy `apps/wave-1/sample-api.yaml.example` to `<app>.yaml`, edit it, then dry-run, then run.

Not verified against a tenant yet: the exact connector attribute names, and whether the schema must be discovered by
ISC instead of written by the pipeline. `python onboarding/scripts/onboard.py --offline` rehearses it without a tenant.
