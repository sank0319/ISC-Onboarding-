# Linux connector

An app with `connectorType: linux` becomes a Linux source in ISC; a virtual appliance reads the Linux server over SSH. There is no feed file.

Folders: `apps/` (app definitions, in `wave-N/` folders), `data/` (empty on purpose), `linux-template.yaml` (this connector's
template: connector script name, settings, schema, correlation).

## Activate (needs the tenant)
1. Find the connector script name: Actions -> **ISC discover (read-only)**, what = `connectors`, query = `linux`.
2. If a working Linux source exists, copy its settings: what = `source`, query = its exact name, template = `linux`.
   Paste the printed block into `linux-template.yaml`. Masked credentials become `secretEnv` entries.
3. Cluster: Required: the virtual appliance cluster that can reach the server over SSH.
4. Add the environment variable named in `secretEnv` (e.g. `LINUX_PASSWORD`) as a secret of the GitHub environment and pass it to the
   run in `.github/workflows/isc-onboarding.yml`. It is only read when a source is created.
5. Copy `apps/wave-1/sample-linux.yaml.example` to `<app>.yaml`, edit it, then dry-run, then run.

Not verified against a tenant yet: the exact connector attribute names, and whether the schema must be discovered by
ISC instead of written by the pipeline. `python onboarding/scripts/onboard.py --offline` rehearses it without a tenant.
