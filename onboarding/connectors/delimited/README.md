# Delimited File connector (`delimited`)

Creates an ISC **Delimited File** source from a CSV account feed, uploads the feed, and makes the entitlements
requestable. This is the only connector already proven on the tenant (Test Delimited App, run #31).

| | |
|---|---|
| `connectorType` | `delimited` |
| ISC connector script | `delimited-file-angularsc` (set in [`delimited-template.yaml`](delimited-template.yaml)) |
| Code | [`onboarding/scripts/connectors/delimited.py`](../../scripts/connectors/delimited.py) |
| Virtual appliance cluster | not needed |
| Feed file | yes, one CSV per app (`data/<env>/<source name>.csv`) |
| Status | verified on the tenant (upload through the File Upload Utility) |

## What is in this folder
| Path | What it is | Rule |
|---|---|---|
| `delimited-template.yaml` | Keys `connectors.delimited` (the ISC connector script name) and `delimitedConnectorAttributes` (`delimiter`, `hasHeader`). Merged into the global defaults at start-up. | Keep the file name `<type>-template.yaml`; it is found by that pattern. |
| `apps/` | One YAML per application, see [`apps/README.md`](apps/README.md). | Must sit in `apps/` or `apps/wave-N/`; `connectorType` must be `delimited`. |
| `data/` | The feed files, see [`data/README.md`](data/README.md). | `data/sandbox/` and `data/production/`; the file name is `<source name>.csv`. |

## How it works
For each app the pipeline runs these steps in order (shared steps live in
[`onboarder.py`](../../scripts/onboarder.py)):

1. **Validate (no API calls).** Checks the config and the CSV, see "Checks before anything is sent" below.
2. **Find or create the source** named `sourceName` (default `appName`). An existing source with the same name and
   connector is reused; one with a different connector stops the app with an error.
3. **Schema and correlation** are written only when the source was just created, or when the app sets
   `reapplyConfig: true`. The schema comes from the CSV header (or `schema.attributes`).
4. **Upload and aggregate.** With `ISC_UPLOAD_JAR` set, SailPoint's File Upload Utility uploads the file and the
   pipeline waits for accounts to appear. Without it the pipeline calls `load-accounts` and waits for the task.
5. **Make entitlements requestable** with approvals Level 1 = manager, Level 2 = source owner.
6. **Create the application and its access profiles** from `application` and `accessProfiles`.

A dry run (`--dry-run`) never writes and never runs the utility. For a source that does not exist yet it stops after step 2 and
reports what it would create; for an existing source the remaining writes are blocked and logged as `[DRY-RUN]`.

## App YAML reference
```yaml
apps:
  - appName: Test-Delimited-App          # required, unique; also the default source name
    connectorType: delimited             # required
    owner: Sanson.Seah                   # required: identity alias or e-mail of the application owner
    application: Test Delimited App      # optional: ISC application to create
    sourceName: Test-Delimited-App       # optional: defaults to appName; also names the feed file
    reapplyConfig: false                 # optional: true = rewrite schema/correlation on an existing source
    delimited:
      accountFile: onboarding/connectors/delimited/data/{env}/Test-Delimited-App.csv   # required
      connectorAttributes: {}            # optional: overrides delimiter/hasHeader from the template
      schema:
        identityAttribute: EmployeeID    # required: CSV column that identifies the account
        displayAttribute: UserName       # required: CSV column shown as the account name
        entitlementColumns: [Role]       # required: columns whose values are entitlements
        multiValued: [Role]              # optional: columns holding several values
        attributes: [..]                 # optional: columns to import (default: the whole header)
      correlation:                       # required: how accounts match identities
        - identityAttr: uid
          accountAttr: UserName
    accessProfiles:
      - name: Test Delimited App - Approver
        entitlementValues: [Approver]    # values that must exist in the feed's entitlement columns
```
`{env}` becomes `production` when the run's `ISC_TARGET` is `production`, otherwise `sandbox`.
A working example is [`apps/wave-1/test-delimited.yaml`](apps/wave-1/test-delimited.yaml).

## Checks before anything is sent
These fail the whole run before the first API call (see `Delimited.validate`):
- `defaults connectors.delimited not set` (the template file is missing or empty)
- `delimited.schema.identityAttribute`, `displayAttribute` or `entitlementColumns` missing
- `delimited.correlation missing`, or an entry without `identityAttr` and `accountAttr`
- `delimited.accountFile` missing, unreadable, empty, with a blank or duplicate header, or no account rows
- a schema, entitlement or correlation column that is not in the CSV header
- the feed path is not `.../connectors/delimited/data/{env}/<source name>.csv` (layout rule in `batch.layout_errors`)

## ISC API calls made
`GET/POST /v3/sources`, `GET/PUT /v3/sources/{id}/schemas`, `GET/PUT /beta/sources/{id}/correlation-config`,
`POST /v2024/sources/{id}/load-accounts` (only without the utility; `/v3/` returned 404 on this tenant),
`GET /beta/task-status/{id}`, `GET /v3/accounts` (utility path), `GET /beta/entitlements`,
`POST /beta/entitlements/bulk-update`, `PUT /beta/entitlements/{id}/entitlement-request-config`,
`GET/POST/PATCH /v3/access-profiles`, `GET/POST/PATCH /beta/source-apps`.

## Credentials
None are stored here. The pipeline's ISC credentials come from HashiCorp Vault (see the main
[README](../../../README.md#credentials-hashicorp-vault)). The feed contains personal data in production:
`data/production/*.csv` is git-ignored.

## Run and test
- Offline, no tenant: `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/delimited/apps/**/*.yaml"`
- Dry run on the tenant: Actions > **ISC Bulk Onboarding**, `target = sandbox`, `dry_run = true`.
- Tests: [`tests/test_validation_files.py`](../../../tests/test_validation_files.py),
  [`tests/test_batch_selection.py`](../../../tests/test_batch_selection.py),
  [`tests/test_uploader.py`](../../../tests/test_uploader.py), [`tests/test_repo_layout.py`](../../../tests/test_repo_layout.py).

## Not yet confirmed
The account count in the ISC UI for run #31 was never checked. Production has no feed or Vault role yet.

## Where to look
Behaviour: [`delimited.py`](../../scripts/connectors/delimited.py) and [`onboarder.py`](../../scripts/onboarder.py).
Upload: [`uploader.py`](../../scripts/uploader.py). Layout decisions: [`docs/structure.md`](../../../docs/structure.md).
Status: [`docs/status.md`](../../../docs/status.md).
