# SQL Loader connector (`sqlloader`)

In this project an app of type `sqlloader` is an ISC **Delimited File** source: ISC has no separate SQL Loader
feed here, so the connector uses the Delimited File script and a predefined template, and the CSV is uploaded.
No virtual appliance cluster is needed (ruling from the project coworker).

| | |
|---|---|
| `connectorType` | `sqlloader` |
| ISC connector script | `delimited-file-angularsc` (in [`sqlloader-template.yaml`](sqlloader-template.yaml)) |
| Code | [`onboarding/scripts/connectors/sqlloader.py`](../../scripts/connectors/sqlloader.py) |
| Virtual appliance cluster | not needed |
| Feed file | yes, one CSV per app (`data/<env>/<source name>.csv`) |
| Status | built and tested offline; not yet run on the tenant (tenant down since about 10 Nov) |

## What is in this folder
| Path | What it is | Rule |
|---|---|---|
| `sqlloader-template.yaml` | `sqlLoaderTemplates.default`: connector script, file settings, schema and correlation that every SQL Loader app inherits. | Keep the name `<type>-template.yaml`. A template can be added next to `default` and chosen with `template:` in the app. |
| `apps/` | App YAMLs; the sample is inactive, see [`apps/README.md`](apps/README.md). | `connectorType: sqlloader`, in `apps/` or `apps/wave-N/`. |
| `data/` | Feed files, see [`data/README.md`](data/README.md). | `data/sandbox/<source name>.csv`; production is supplied at run time. |

## How it works
1. **Validate offline**: the template exists, the connector is not a `REPLACE_*` placeholder, schema and
   correlation are present, the CSV exists, has rows, and contains every schema, entitlement and correlation column.
2. **Create or reuse the source** (same name and connector reuses it; a different connector is an error).
3. **Schema and correlation** are applied from the template when the source is new, or when `reapplyConfig: true`.
4. **Upload and aggregate**: through SailPoint's File Upload Utility when `ISC_UPLOAD_JAR` is set (the file is copied
   to `<sourceID>-<name>.csv`; credentials reach the JAR as environment variables), otherwise `load-accounts`.
5. **Requestable entitlements** (approvals Level 1 manager, Level 2 source owner), then the **application** and its
   **access profiles**.

## App YAML reference
```yaml
apps:
  - appName: Sample-SqlLoader-App
    connectorType: sqlloader
    owner: Sanson.Seah                   # required
    template: default                    # optional: which sqlLoaderTemplates entry (default: "default")
    application: Sample SqlLoader App    # optional
    sqlloader:
      accountFile: onboarding/connectors/sqlloader/data/{env}/Sample-SqlLoader-App.csv   # required
      # any template key can be overridden here, e.g. connectorAttributes or schema
    accessProfiles:
      - name: Sample SqlLoader App - Approver
        entitlementValues: [APPROVER]
```
Template defaults: schema `USER_ID, USER_NAME, EMAIL, STATUS, ROLE` (identity `USER_ID`, display `USER_NAME`,
entitlement column `ROLE`) and correlation `uid` to `USER_ID`. The sample feed is
[`data/sandbox/Sample-SqlLoader-App.csv`](data/sandbox/Sample-SqlLoader-App.csv).

## Turn it on
1. Copy `apps/wave-1/sample-sqlloader.yaml.example` to `<app>.yaml` (drop `.example`; only `*.yaml` is read) and edit
   `appName`, `owner`, `application` and the access profiles.
2. Put the real feed in `data/sandbox/<source name>.csv` and match the template's schema columns to it
   (edit `sqlloader-template.yaml`, or override `schema` in the app).
3. Optional: switch on the File Upload Utility (below).
4. Run Actions > **ISC Bulk Onboarding**, `target = sandbox`, first with `dry_run = true`, then for real.
5. Check in ISC that the source exists, the accounts loaded and the access profiles are attached.

## File Upload Utility (optional switch)
Set two variables on the GitHub environment: `FILE_UPLOAD_JAR_URL` (the release asset from
`github.com/sailpoint-oss/colab-file-upload-utility`, version 4.1.0 or newer) and `FILE_UPLOAD_JAR_SHA256` (its checksum;
the run refuses to continue on a mismatch). The apply job then installs Java 17, downloads and verifies the JAR, and the
pipeline runs it for every feed. Dry runs never run it. Details: [`uploader.py`](../../scripts/uploader.py).

## Checks before anything is sent
`sqlLoaderTemplates.<name> not found`, connector script name not set (`REPLACE_*`), schema or correlation missing,
`sqlloader.accountFile missing`, unreadable or empty CSV, any schema, entitlement or correlation column not in the header,
and the feed path rule `.../connectors/sqlloader/data/{env}/<source name>.csv`.

## ISC API calls made
Same as the Delimited File connector: [`../delimited/README.md`](../delimited/README.md#isc-api-calls-made).

## Not yet confirmed
- The dry run and real run on the tenant.
- Whether the real feed has other columns than the template's five.
- Whether the source needs query-style settings in `connectorAttributes`; if so, read a working source with
  Actions > **ISC discover (read-only)** (`what = source`, `template = sqlloader`) and copy the printed block into
  `sqlloader-template.yaml`. Masked values must never be committed.

## Run and test
- Offline: `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/sqlloader/apps/**/*.yaml*"`
- Tests: [`tests/test_validation_files.py`](../../../tests/test_validation_files.py),
  [`tests/test_uploader.py`](../../../tests/test_uploader.py), [`tests/test_repo_layout.py`](../../../tests/test_repo_layout.py).

## Where to look
Code: [`sqlloader.py`](../../scripts/connectors/sqlloader.py). Shared steps: [`onboarder.py`](../../scripts/onboarder.py).
Discovery: [`discover.py`](../../scripts/discover.py). Status: [`docs/status.md`](../../../docs/status.md).
