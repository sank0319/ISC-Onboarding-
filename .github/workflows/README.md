# `.github/workflows/`: the automation

Four GitHub Actions workflows. GitHub only runs workflows from this exact folder. They are kept in separate files so a
read-only lookup can never share a file (or a token) with something that writes to ISC.

| Workflow | File | Trigger | Writes to ISC? | Needs the tenant? |
|---|---|---|---|---|
| **ISC Bulk Onboarding** | [`isc-onboarding.yml`](isc-onboarding.yml) | pull request touching `onboarding/**` (not `onboarding/logs/**`); manual | only the manual `apply` job with `dry_run = false` | yes |
| **ISC connectivity test** | [`isc-connectivity-test.yml`](isc-connectivity-test.yml) | manual | no | yes |
| **ISC discover (read-only)** | [`isc-discover.yml`](isc-discover.yml) | manual | no (GET only) | yes |
| **Offline rehearsal** | [`isc-rehearsal.yml`](isc-rehearsal.yml) | pull request touching `onboarding/**`, `tests/**`, `requirements*.txt` or itself; manual | no | **no** |

## ISC Bulk Onboarding
Jobs:
- **`validate`**: `onboard.py --validate-only`; offline, no credentials. Runs on every trigger.
- **`plan`** (pull requests only): environment `sandbox-readonly`; `onboard.py --dry-run`; uploads `plan-report` and `plan-logs`.
  It never pushes anything.
- **`apply`** (manual only): environment = the `target` input; optional File Upload Utility install; runs `onboard.py`;
  uploads `onboarding-report-<target>` and `onboarding-logs-<target>`; commits new run logs to the branch.

Manual inputs: `target` (`sandbox` or `production`), `apps` (glob, default `onboarding/connectors/*/apps/**/*.yaml`), `wave`
(empty = all), `apps_csv` (optional selection file), `dry_run` (default **true**).
Runs are serialized per target (`concurrency`).

## ISC connectivity test
Prints the OIDC claims of the job (`iss`, `sub`, `aud`; not secret) and then logs in to Vault, reads the secret, gets an ISC
token and lists 5 source names. Use it first on a new environment, and to copy the exact `sub` into the Vault role.
Input: `target` (`sandbox-readonly`, `sandbox`, `production`).

## ISC discover (read-only)
Inputs: `target` (`sandbox-readonly`, `sandbox`), `what` (`connectors`, `sources`, `source`), `template` (`sqlloader`, `jdbc`,
`api`, `windows`, `linux`; used with `what = source`), `query` (text to look for, or the exact source name).
Output goes to the job log; credential-looking values are masked, but read it before sharing it.

## Offline rehearsal
Installs `requirements.txt` and `requirements-dev.txt`, runs `python -m pytest -q tests`, then
`onboard.py --offline --apps "onboarding/connectors/*/apps/**/*.yaml*"`, which rehearses every config **including the
inactive `.yaml.example` samples** against the in-memory simulator. It uses no credentials.

## GitHub environments, variables and secrets
Create three **environments**: `sandbox-readonly`, `sandbox`, `production` (add required reviewers to `production`).

| Name | Kind | Used for |
|---|---|---|
| `VAULT_ADDR`, `VAULT_ROLE`, `VAULT_SECRET_PATH` | variable | Vault login and the secret to read (one set per environment) |
| `VAULT_NAMESPACE` | variable | `admin` on HCP Vault Dedicated, empty otherwise |
| `VAULT_AUTH_MOUNT`, `VAULT_KV_MOUNT` | variable, optional | defaults `jwt` and `secret` |
| `ISC_BASE_URL` | variable, optional | API base URL for tenants outside `identitynow.com` (also a key in the Vault secret) |
| `FILE_UPLOAD_JAR_URL`, `FILE_UPLOAD_JAR_SHA256` | variable, optional | switch on the File Upload Utility (URL must start `https://github.com/sailpoint-oss/`; the checksum is mandatory) |
| `JDBC_PASSWORD` | secret | database password for JDBC apps (stopgap; only read when a source is created) |
| `ISC_TENANT`, `ISC_CLIENT_ID`, `ISC_CLIENT_SECRET` | secret, **legacy** | fallback when `VAULT_ADDR` is unset; delete once a Vault run has passed |

Every job that talks to ISC has `permissions: id-token: write` so it can request the GitHub OIDC token that Vault verifies.
Details of the Vault setup: the main [README](../../README.md#credentials-hashicorp-vault).

## Rules for editing a workflow
- Keep `id-token: write` on jobs that load credentials, and `contents: write` only on the job that commits logs.
- Never echo a secret or put one in a command line; pass it through `env:`.
- A new secret that a connector reads through `secretEnv` (for example `API_TOKEN`, `WINDOWS_PASSWORD`, `LINUX_PASSWORD`)
  must be added to the **Onboard apps** step's `env:` block in `isc-onboarding.yml`; only `JDBC_PASSWORD` is wired today.
- After a change, run the **Offline rehearsal** workflow and a dry run on `sandbox`.

## Where to look
The code these workflows start: [`onboarding/scripts/`](../../onboarding/scripts/README.md). Status: [`docs/status.md`](../../docs/status.md).
