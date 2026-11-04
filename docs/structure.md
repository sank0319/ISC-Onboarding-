# Repository structure - decisions from the 2026-11-04 meeting

Source: [meeting notes](meeting-notes/2026-11-04-utility-structure-and-migration.md) (verbatim).
The notes describe the server-side migration utilities; this repo is the CI/CD pipeline, so only the parts that
shape app configs, feed files and pipeline inputs are applied.

## Layout

```
onboarding/
  config/defaults.yaml
  connectors/                      app configs + feed data, one folder per connector type
    delimited/
      wave-1/test-delimited.yaml   wave 1 = one pilot app
      wave-2/                      wave 2 = all remaining apps (created with the first remaining app)
      feeds/sandbox/<source name>.csv
      feeds/production/            filled at run time from secure storage (*.csv git-ignored)
    sqlloader/                     same shape: wave-1/, wave-2/, feeds/ if it is file fed
    ad/                            no waves: <app>.yaml next to feeds/{sandbox,production}/<app>-group.csv
  scripts/
    onboard.py                     CLI entrypoint (main)
    onboarder.py                   shared ISC operations + per-app runner
    batch.py                       load / wave / apps CSV / layout check / validation of a batch
    common.py, validation.py       shared helpers
    connectors/                    one file per connector: delimited.py, sqlloader.py, ad.py, _template.py
docs/examples/                     batch-01.yaml (example, outside the default glob), apps.csv, sample feeds
```

Folders for `sqlloader` and `ad` are created with their first real app (git does not keep empty folders).

## Notes -> decisions

| Meeting note | Decision |
|---|---|
| Feed files in separate production and sandbox folders | `feeds/sandbox/` and `feeds/production/` inside each connector folder. Feed paths in app YAML use `{env}`; `onboard.py` fills it from `ISC_TARGET` (`production` -> production, anything else -> sandbox). A sandbox run can never read a production feed and vice versa. Production `*.csv` are git-ignored (personal data). |
| Incoming files `source name.csv`, group files `source name-group.csv` | Enforced at validation (`batch.layout_errors`) for configs under `onboarding/connectors/`: account feed = `<sourceName or appName>.csv`; group/entitlement export = `<name>-group.csv` (AD uses the app name for now). |
| Organise application files into connector-based folders, aggregation data inside them | `onboarding/connectors/<type>/`; a config must sit in the folder of its `connectorType` (checked). Old `onboarding/apps/` and `onboarding/data/` are gone. |
| Separate waves for SQL Loader; delimited has two waves (1 = test one app, 2 = the rest) | `wave-<N>` sub-folders. `--wave N` (workflow input `wave`) runs one wave; no option = all. Waves are a folder convention, so no schema change. |
| Application CSV with application name and type as input; selected apps are created | `--apps-csv file.csv` (workflow input `apps_csv`), columns `applicationName`, `applicationType`. Selects among the configs found, runs them in CSV order. Missing or unknown names, unknown types, type mismatch, duplicates and bad headers stop the run before any API call. |
| Pipeline script calls the stages in sequence | Already how `onboard.py` works (validate -> per app steps). No extra wrapper script. |
| Utilities for sandbox and production; scripts, logs, config, secrets | Scripts in `onboarding/scripts`, config in `onboarding/config`, logs in `onboarding/logs` (bot-committed, untouched), secrets in GitHub environments. Sandbox/production separation is the GitHub environment plus the `{env}` feed folder; no second copy of the scripts. |
| Simple validation screenshots now, full SIT later | [validation-evidence.md](validation-evidence.md). |
| (requirement from the team) every future connector gets its own file | `scripts/connectors/<type>.py` auto-registered; JDBC, API, Windows/Linux = add one file plus one config folder. See README "Adding a connector". |

## Deliberately not implemented

| Note | Reason |
|---|---|
| IIQ Easy Transfer (IIQ XML -> CSV), IIQ Export, IIQ Import | Server-side migration tooling. Chandra: CI/CD does not need it, only the CSV files. |
| Sequential analyze / transfer / validate / load / scorecard stages | Same: part of the IIQ migration, not of this pipeline. |
| Migration application-creation and dependency files | Chandra: not required by CI/CD. |
| Temp files, file state, secrets storage folders | The pipeline writes none (state is in ISC; secrets are GitHub secrets). Nothing added to `.gitignore` for them. |
| Active Directory layout | Chandra: AD is implemented differently; to be discussed with Abhijeet. AD keeps its current behaviour and a provisional folder shape (`ad/`, `-group.csv`); revisit after that call. |
| "Views" split during creation | Covered by waves/folders; no separate concept. |
| 2HK 14-day period and manual process | Process discussion with the LBUs, not code. |
| Full SIT | Later phase; only the simple evidence checklist is added. |

## Behaviour changes to be aware of

- The default `--apps` glob is now `onboarding/connectors/**/*.yaml` (recursive). `batch-01.yaml` is no longer in it.
- The pilot app's feed moved to `feeds/sandbox/`; a production run needs `feeds/production/Test-Delimited-App.csv` (or its own app) and fails validation, before any API call, if it is missing.
