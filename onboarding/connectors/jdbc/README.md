# JDBC connector (`jdbc`)

Creates a database source: the virtual appliance (VA) connects to the database and reads the account table. There is **no feed file**: after the source is created ISC pulls the data itself, so
`data/` only holds a note. It shares one implementation with the other template-driven connectors (JDBC, API, Windows, Linux)
([`_sourcetemplate.py`](../../scripts/connectors/_sourcetemplate.py)); this file is a few lines that set the
type, the template key and the cluster rule.

| | |
|---|---|
| `connectorType` | `jdbc` |
| Template key | `jdbcTemplates` in [`jdbc-template.yaml`](jdbc-template.yaml) |
| Code | [`onboarding/scripts/connectors/jdbc.py`](../../scripts/connectors/jdbc.py) (base: `_sourcetemplate.py`) |
| Virtual appliance cluster | **required**: the source runs on a virtual appliance cluster |
| Feed file | none |
| Credential | environment variable named in `secretEnv` (the sample uses `JDBC_PASSWORD`; the code has no default name), never YAML |
| Status | built and tested offline with made-up hosts; not run on the tenant |

## What is in this folder
| Path | What it is | Rule |
|---|---|---|
| `jdbc-template.yaml` | `jdbcTemplates.default`: connector script name, `clusterId`, `connectorAttributes`, `secretEnv`, schema and correlation. | Keep the name `<type>-template.yaml`. Until the script name is replaced (`REPLACE_WITH_JDBC_SCRIPT_NAME`), every `jdbc` app fails validation on purpose. |
| `apps/` | App YAMLs; the sample is inactive, see [`apps/README.md`](apps/README.md). | `connectorType: jdbc`, in `apps/` or `apps/wave-N/`. |
| `data/` | A note only, see [`data/README.md`](data/README.md). | Keep the folder so every connector has the same shape. |

## How it works
1. **Validate offline** (see below).
2. **Read secrets from the environment** for every `secretEnv` entry. This happens **only when the source does not
   exist yet** and never in a dry run; a missing variable stops the app with a clear error.
3. **Create or reuse the source** named `sourceName` (default `appName`), on the cluster if one is set.
4. **Schema and correlation** are written from the template when the source is new, or when `reapplyConfig: true`.
5. **Aggregate** with no file (ISC reads the target itself) and wait for the task.
6. **Requestable entitlements** (approvals Level 1 manager, Level 2 source owner), then the **application** and the
   **access profiles**.

## Template reference (`jdbc-template.yaml`)
```yaml
jdbcTemplates:
  default:
    connector: REPLACE_WITH_JDBC_SCRIPT_NAME   # replace with the real script name (discover: what = connectors, query = jdbc)
    clusterId: ""            # `jdbcTemplates.<name>.clusterId` or the global `clusterId`
    connectorAttributes: {}   # `url` (checked: must start with `jdbc:`), `user`, driver class, account query (the real attribute names must be copied from a working source)
    secretEnv: {}            # { password: JDBC_PASSWORD }: connector attribute -> environment variable name
    schema:
      identityAttribute: USER_ID
      displayAttribute: USER_NAME
      attributes: [..]
      entitlementColumns: [..]
    correlation:
      - identityAttr: uid
        accountAttr: USER_ID
```
An app can override any of these under its own `jdbc:` block; mappings are merged, so the app only lists the differences.

## App YAML reference
```yaml
apps:
  - appName: Sample-Jdbc-App
    connectorType: jdbc
    owner: Sanson.Seah                 # required
    template: default                  # optional: which jdbcTemplates entry
    application: Sample App            # optional
    jdbc:
      connectorAttributes:             # merged onto the template; no secrets here
        url: jdbc:postgresql://hr-db01.example.invalid:5432/hrdb
        user: svc_isc_readonly
      secretEnv:
        password: JDBC_PASSWORD   # environment variable that holds the secret
    accessProfiles:
      - name: Sample App - Approver
        entitlementValues: [APPROVER]
```
The inactive sample is [`apps/wave-1/sample-jdbc.yaml.example`](apps/wave-1/sample-jdbc.yaml.example); its host names are made up
(`.invalid` never resolves), as agreed with the coworker for config-only work.

## Secrets
- Put **no** password, token, key or secret in YAML. A `connectorAttributes` key containing `pass`, `secret`, `token`,
  `credential` or `private` with a value fails validation.
- `secretEnv` maps a connector attribute to an **environment variable name** (upper case, e.g. `JDBC_PASSWORD`).
- Wiring: `JDBC_PASSWORD` is already passed to the apply job by `isc-onboarding.yml` (from the GitHub environment secret of the same name).
- Secret values are registered with the log redactor, so they are masked in logs and reports.

## Checks before anything is sent
`jdbcTemplates.<name> not found`; connector script name not set (`REPLACE_*`); cluster not set where required;
`connectorAttributes` or `secretEnv` not a mapping; an invalid environment variable name; a credential-looking attribute
holding a value; `connectorAttributes.url` must start with `jdbc:`. schema `identityAttribute`, `displayAttribute` or `attributes` missing; correlation missing;
and the common source/application/access-profile checks (names, owner, profile entries).

## Turn it on
1. Find the script name: Actions > **ISC discover (read-only)**, `what = connectors`, `query = jdbc`.
2. Copy the settings from a working JDBC source: `what = source`, `query = <exact source name>`,
   `template = jdbc`. The output is a ready block for `jdbc-template.yaml`; masked values become `secretEnv` entries.
3. Set `connector`, `clusterId` (if needed) and the attributes in `jdbc-template.yaml`.
4. Make the secret available as the environment variable (see Secrets).
5. Copy the sample to `apps/wave-1/<app>.yaml`, edit it, run **ISC Bulk Onboarding** on `sandbox` with `dry_run = true`,
   then for real.

## Not yet confirmed
The exact connector attribute names (URL, driver, account query) and whether the pipeline should write the schema or ISC should discover it from the query; the real script name and cluster ID; where the database password should live (GitHub secret `JDBC_PASSWORD` is the stopgap, Vault is the alternative).

## Run and test
- Offline: `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/jdbc/apps/**/*.yaml*"`
- Tests: [`tests/test_template_sources.py`](../../../tests/test_template_sources.py) and [`tests/test_jdbc.py`](../../../tests/test_jdbc.py),
  [`tests/test_offline.py`](../../../tests/test_offline.py), [`tests/test_repo_layout.py`](../../../tests/test_repo_layout.py).

## Where to look
[`jdbc.py`](../../scripts/connectors/jdbc.py), [`_sourcetemplate.py`](../../scripts/connectors/_sourcetemplate.py) (all checks and
the create flow), [`discover.py`](../../scripts/discover.py) (reading a working source), [`docs/status.md`](../../../docs/status.md).
