# Linux connector (`linux`)

Creates a Linux server source read over SSH through a virtual appliance. There is **no feed file**: after the source is created ISC pulls the data itself, so
`data/` only holds a note. It shares one implementation with the other template-driven connectors (JDBC, API, Windows, Linux)
([`_sourcetemplate.py`](../../scripts/connectors/_sourcetemplate.py)); this file is a few lines that set the
type, the template key and the cluster rule.

| | |
|---|---|
| `connectorType` | `linux` |
| Template key | `linuxTemplates` in [`linux-template.yaml`](linux-template.yaml) |
| Code | [`onboarding/scripts/connectors/linux.py`](../../scripts/connectors/linux.py) (base: `_sourcetemplate.py`) |
| Virtual appliance cluster | **required**: the source runs on a virtual appliance cluster |
| Feed file | none |
| Credential | environment variable named in `secretEnv` (the sample uses `LINUX_PASSWORD`; the code has no default name), never YAML |
| Status | built and tested offline with a made-up host; not run on the tenant |

## What is in this folder
| Path | What it is | Rule |
|---|---|---|
| `linux-template.yaml` | `linuxTemplates.default`: connector script name, `clusterId`, `connectorAttributes`, `secretEnv`, schema and correlation. | Keep the name `<type>-template.yaml`. Until the script name is replaced (`REPLACE_WITH_LINUX_SCRIPT_NAME`), every `linux` app fails validation on purpose. |
| `apps/` | App YAMLs; the sample is inactive, see [`apps/README.md`](apps/README.md). | `connectorType: linux`, in `apps/` or `apps/wave-N/`. |
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

## Template reference (`linux-template.yaml`)
```yaml
linuxTemplates:
  default:
    connector: REPLACE_WITH_LINUX_SCRIPT_NAME   # replace with the real script name (discover: what = connectors, query = linux)
    clusterId: ""            # `linuxTemplates.<name>.clusterId` or the global `clusterId`
    connectorAttributes: {}   # host, port, user and authentication method (the real attribute names must be copied from a working source)
    secretEnv: {}            # { password: LINUX_PASSWORD }: connector attribute -> environment variable name
    schema:
      identityAttribute: USER_ID
      displayAttribute: USER_NAME
      attributes: [..]
      entitlementColumns: [..]
    correlation:
      - identityAttr: uid
        accountAttr: USER_ID
```
An app can override any of these under its own `linux:` block; mappings are merged, so the app only lists the differences.

## App YAML reference
```yaml
apps:
  - appName: Sample-Linux-App
    connectorType: linux
    owner: Sanson.Seah                 # required
    template: default                  # optional: which linuxTemplates entry
    application: Sample App            # optional
    linux:
      connectorAttributes:             # merged onto the template; no secrets here
        host: lnx-srv01.example.invalid
        port: 22
        user: svc_isc_readonly
      secretEnv:
        password: LINUX_PASSWORD   # environment variable that holds the secret
    accessProfiles:
      - name: Sample App - Approver
        entitlementValues: [APPROVER]
```
The inactive sample is [`apps/wave-1/sample-linux.yaml.example`](apps/wave-1/sample-linux.yaml.example); its host names are made up
(`.invalid` never resolves), as agreed with the coworker for config-only work.

## Secrets
- Put **no** password, token, key or secret in YAML. A `connectorAttributes` key containing `pass`, `secret`, `token`,
  `credential` or `private` with a value fails validation.
- `secretEnv` maps a connector attribute to an **environment variable name** (upper case, e.g. `LINUX_PASSWORD`).
- Wiring: Not wired yet: `isc-onboarding.yml` passes only `JDBC_PASSWORD`. Add `LINUX_PASSWORD: ${{ secrets.LINUX_PASSWORD }}` to the **Onboard apps** step's `env:` block, and create the secret on the GitHub environment.
- Secret values are registered with the log redactor, so they are masked in logs and reports.

## Checks before anything is sent
`linuxTemplates.<name> not found`; connector script name not set (`REPLACE_*`); cluster not set where required;
`connectorAttributes` or `secretEnv` not a mapping; an invalid environment variable name; a credential-looking attribute
holding a value; no URL check (only JDBC checks `url`). schema `identityAttribute`, `displayAttribute` or `attributes` missing; correlation missing;
and the common source/application/access-profile checks (names, owner, profile entries).

## Turn it on
1. Find the script name: Actions > **ISC discover (read-only)**, `what = connectors`, `query = linux`.
2. Copy the settings from a working Linux source: `what = source`, `query = <exact source name>`,
   `template = linux`. The output is a ready block for `linux-template.yaml`; masked values become `secretEnv` entries.
3. Set `connector`, `clusterId` (if needed) and the attributes in `linux-template.yaml`.
4. Make the secret available as the environment variable (see Secrets).
5. Copy the sample to `apps/wave-1/<app>.yaml`, edit it, run **ISC Bulk Onboarding** on `sandbox` with `dry_run = true`,
   then for real.

## Not yet confirmed
The Linux connector script name and attribute names, the cluster ID, whether authentication is a password or a key, and the schema approach (the template's columns are a guess).

## Run and test
- Offline: `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/linux/apps/**/*.yaml*"`
- Tests: [`tests/test_template_sources.py`](../../../tests/test_template_sources.py),
  [`tests/test_offline.py`](../../../tests/test_offline.py), [`tests/test_repo_layout.py`](../../../tests/test_repo_layout.py).

## Where to look
[`linux.py`](../../scripts/connectors/linux.py), [`_sourcetemplate.py`](../../scripts/connectors/_sourcetemplate.py) (all checks and
the create flow), [`discover.py`](../../scripts/discover.py) (reading a working source), [`docs/status.md`](../../../docs/status.md).
