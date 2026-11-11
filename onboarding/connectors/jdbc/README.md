# JDBC connector

An app with `connectorType: jdbc` becomes a JDBC source on a virtual appliance. The appliance reads the database,
so there is no feed file (`data/` is empty on purpose).

## Activate (needs the tenant)
1. Find the connector script name: Actions -> **ISC discover (read-only)**, what = `connectors`, query = `jdbc`.
2. Copy settings from a working JDBC source: what = `source`, query = its exact name, and set the template key to `jdbcTemplates`
   (see the discover workflow input). Paste the printed block into `onboarding/config/defaults.yaml`.
   Masked credential values become `secretEnv` entries (attribute -> environment variable name).
3. Set `clusterId` (the virtual appliance cluster).
4. Make the database password available to the run: add the environment variable named in `secretEnv`
   (default `JDBC_PASSWORD`) as a secret of the GitHub environment. It is read only when a source is created.
5. Copy `apps/wave-1/sample-jdbc.yaml.example` to `<app>.yaml`, edit it, then dry-run, then run.

Not verified against the tenant yet: the exact connector attribute names and whether the schema must be
discovered from the query instead of written by the pipeline.
