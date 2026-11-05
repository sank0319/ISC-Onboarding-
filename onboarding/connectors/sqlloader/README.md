# SQL Loader connector

```
apps/wave-1/    the app definitions that create the applications (wave 1 = pilot, wave 2 = the rest)
data/sandbox/   feed files (<source name>.csv, group files <source name>-group.csv)
data/production/  supplied at run time; *.csv here is git-ignored
```
The code is `onboarding/scripts/connectors/sqlloader.py` (source from template, schema, correlation,
aggregation, requestable entitlements with approvals L1 Manager -> L2 Source Owner).

## Turn it on (needs one working SQL Loader source in the tenant)
1. In ISC, or with `GET /v3/sources/{id}` in Postman, open an existing SQL Loader source and note its
   `connector` (the script name), `connectorAttributes`, and the VA cluster id.
2. In `onboarding/config/defaults.yaml` set `sqlLoaderTemplates.default.connector` (replace
   `REPLACE_WITH_SQL_LOADER_SCRIPT_NAME`), `connectorAttributes` and `clusterId`, and check the template's
   `schema` and `correlation` match your files.
3. Rename `apps/wave-1/sample-sqlloader.yaml.example` to `<app>.yaml` and edit `appName`, `owner`,
   `application` and `sqlloader.connectorAttributes.file`.
4. Run **ISC Bulk Onboarding** on `sandbox` with dry run on, then for real.

Until step 2 is done, any `sqlloader` app fails validation with "SQL Loader connector script name not set in defaults".

## Notes
- Do not commit passwords: copy only non-secret keys from `connectorAttributes`.
- The pipeline does not upload files for SQL Loader. The source reads the file at the path in
  `connectorAttributes.file` on the VA, so `data/` is where the sandbox copy is kept for handover and review.
