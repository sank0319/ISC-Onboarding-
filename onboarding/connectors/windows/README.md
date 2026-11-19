# Windows connector (`windows`)

Creates a Windows server or domain source read through a virtual appliance. There is **no feed file**: after the source is created ISC pulls the data itself, so
`data/` only holds a note. It shares one implementation with the other template-driven connectors (JDBC, API, Windows, Linux)
([`_sourcetemplate.py`](../../scripts/connectors/_sourcetemplate.py)); this file is a few lines that set the
type, the template key and the cluster rule.

| | |
|---|---|
| `connectorType` | `windows` |
| Template key | `windowsTemplates` in [`windows-template.yaml`](windows-template.yaml) |
| Code | [`onboarding/scripts/connectors/windows.py`](../../scripts/connectors/windows.py) (base: `_sourcetemplate.py`) |
| Virtual appliance cluster | **required**: the source runs on a virtual appliance cluster |
| Feed file | none |
| Credential | environment variable named in `secretEnv` (the sample uses `WINDOWS_PASSWORD`; the code has no default name), never YAML |
| Status | built and tested offline with a made-up host; not run on the tenant |

## What is in this folder
| Path | What it is | Rule |
|---|---|---|
| `windows-template.yaml` | `windowsTemplates.default`: connector script name, `clusterId`, `connectorAttributes`, `secretEnv`, schema and correlation. | Keep the name `<type>-template.yaml`. Until the script name is replaced (`REPLACE_WITH_WINDOWS_SCRIPT_NAME`), every `windows` app fails validation on purpose. |
| `apps/` | App YAMLs; the sample is inactive, see [`apps/README.md`](apps/README.md). | `connectorType: windows`, in `apps/` or `apps/wave-N/`. |
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

## Template reference (`windows-template.yaml`)
```yaml
windowsTemplates:
  default:
    connector: REPLACE_WITH_WINDOWS_SCRIPT_NAME   # replace with the real script name (discover: what = connectors, query = windows)
    clusterId: ""            # `windowsTemplates.<name>.clusterId` or the global `clusterId`
    connectorAttributes: {}   # host, domain and service account (the real attribute names must be copied from a working source)
    secretEnv: {}            # { password: WINDOWS_PASSWORD }: connector attribute -> environment variable name
    schema:
      identityAttribute: USER_ID
      displayAttribute: USER_NAME
      attributes: [..]
      entitlementColumns: [..]
    correlation:
      - identityAttr: uid
        accountAttr: USER_ID
```
An app can override any of these under its own `windows:` block; mappings are merged, so the app only lists the differences.

## App YAML reference
```yaml
apps:
  - appName: Sample-Windows-App
    connectorType: windows
    owner: Sanson.Seah                 # required
    template: default                  # optional: which windowsTemplates entry
    application: Sample App            # optional
    windows:
      connectorAttributes:             # merged onto the template; no secrets here
        host: win-srv01.example.invalid
        user: svc_isc_readonly
      secretEnv:
        password: WINDOWS_PASSWORD   # environment variable that holds the secret
    accessProfiles:
      - name: Sample App - Approver
        entitlementValues: [APPROVER]
```
The inactive sample is [`apps/wave-1/sample-windows.yaml.example`](apps/wave-1/sample-windows.yaml.example); its host names are made up
(`.invalid` never resolves), as agreed with the coworker for config-only work.

## Secrets
- Put **no** password, token, key or secret in YAML. A `connectorAttributes` key containing `pass`, `secret`, `token`,
  `credential` or `private` with a value fails validation.
- `secretEnv` maps a connector attribute to an **environment variable name** (upper case, e.g. `WINDOWS_PASSWORD`).
- Wiring: Not wired yet: `isc-onboarding.yml` passes only `JDBC_PASSWORD`. Add `WINDOWS_PASSWORD: ${{ secrets.WINDOWS_PASSWORD }}` to the **Onboard apps** step's `env:` block, and create the secret on the GitHub environment.
- Secret values are registered with the log redactor, so they are masked in logs and reports.

## Checks before anything is sent
`windowsTemplates.<name> not found`; connector script name not set (`REPLACE_*`); cluster not set where required;
`connectorAttributes` or `secretEnv` not a mapping; an invalid environment variable name; a credential-looking attribute
holding a value; no URL check (only JDBC checks `url`). schema `identityAttribute`, `displayAttribute` or `attributes` missing; correlation missing;
and the common source/application/access-profile checks (names, owner, profile entries).

## Turn it on
1. Find the script name: Actions > **ISC discover (read-only)**, `what = connectors`, `query = windows`.
2. Copy the settings from a working Windows source: `what = source`, `query = <exact source name>`,
   `template = windows`. The output is a ready block for `windows-template.yaml`; masked values become `secretEnv` entries.
3. Set `connector`, `clusterId` (if needed) and the attributes in `windows-template.yaml`.
4. Make the secret available as the environment variable (see Secrets).
5. Copy the sample to `apps/wave-1/<app>.yaml`, edit it, run **ISC Bulk Onboarding** on `sandbox` with `dry_run = true`,
   then for real.

## Not yet confirmed
The Windows connector script name and attribute names, the cluster ID, and the schema approach (the template's `USER_ID, USER_NAME, STATUS, GROUP` columns are a guess).

## Run and test
- Offline: `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/windows/apps/**/*.yaml*"`
- Tests: [`tests/test_template_sources.py`](../../../tests/test_template_sources.py),
  [`tests/test_offline.py`](../../../tests/test_offline.py), [`tests/test_repo_layout.py`](../../../tests/test_repo_layout.py).

## Where to look
[`windows.py`](../../scripts/connectors/windows.py), [`_sourcetemplate.py`](../../scripts/connectors/_sourcetemplate.py) (all checks and
the create flow), [`discover.py`](../../scripts/discover.py) (reading a working source), [`docs/status.md`](../../../docs/status.md).
