# `onboarding/connectors/`: one folder per connector type

Everything about one connector lives in its own folder, and every folder has the same shape. Code for each connector is
one file in [`onboarding/scripts/connectors/`](../scripts/connectors/README.md).

```
<type>/
  <type>-template.yaml   the connector's shared settings (script name, attributes, schema, correlation, secretEnv)
  README.md              how the connector works, its keys, its checks and how to turn it on
  apps/                  application definitions (README.md inside)
    wave-1/              wave 1 = the pilot app
    wave-2/              wave 2 = everything after the pilot
  data/                  feed files (README.md inside); sandbox/ and production/ where the connector has a feed
```

## The connectors
Status as of 2026-11-19. "Verified" means seen working on the tenant; "built" means written and passing the offline
tests and the offline rehearsal only. Details: [`docs/status.md`](../../docs/status.md).

| Folder | `connectorType` | ISC connector | Feed | Cluster | Status | Docs |
|---|---|---|---|---|---|---|
| `delimited/` | `delimited` | Delimited File | CSV, uploaded | no | **verified** (utility upload) | [README](delimited/README.md) |
| `sqlloader/` | `sqlloader` | Delimited File (project convention) | CSV, uploaded | no | built | [README](sqlloader/README.md) |
| `ad/` | `ad` | existing AD source (none created) | group export `<app>-group.csv` | n/a | built | [README](ad/README.md) |
| `jdbc/` | `jdbc` | JDBC | none (appliance reads the DB) | yes | built; needs script name and cluster | [README](jdbc/README.md) |
| `api/` | `api` | web services | none | normally no | built; needs script name | [README](api/README.md) |
| `windows/` | `windows` | Windows | none | yes | built; needs script name and cluster | [README](windows/README.md) |
| `linux/` | `linux` | Linux (SSH) | none | yes | built; needs script name and cluster | [README](linux/README.md) |

## Rules every connector folder follows
These are enforced by `batch.layout_errors`, `tests/test_repo_layout.py` and `tests/test_template_sources.py`.
- App YAMLs sit in `<type>/apps/` or `<type>/apps/wave-N/`, and their `connectorType` equals the folder name. AD has no waves: its apps sit directly in `ad/apps/`.
- A feed path in an app is `onboarding/connectors/<type>/data/{env}/<name>`, where `<name>` is `<source name>.csv`
  (accounts) or `<app name>-group.csv` (AD groups).
- Template file name is `<type>-template.yaml`; the settings of all templates are merged with the global defaults.
- Samples end in `.yaml.example`, so they stay inactive until copied and renamed.
- Connectors without a feed keep an empty `data/` with a README, so the shape never differs.
- No secret in any YAML: use `secretEnv` to name an environment variable.
- `data/production/*.csv` is git-ignored.

## Add a connector folder
1. Write the code file (see [`scripts/connectors/README.md`](../scripts/connectors/README.md)).
2. Copy an existing folder of the same kind (for example `jdbc/` for a template-driven one) and rename the files.
3. Add a row to the table above and to the main [`README.md`](../../README.md); add the type to the known-types assertions
   in `tests/test_connector_registry.py`.
4. Run `python -m pytest -q tests`.

## Work without a tenant
`python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/*/apps/**/*.yaml*"` rehearses every config and
inactive sample against an in-memory simulator (also run on every pull request by the *Offline rehearsal* workflow).
