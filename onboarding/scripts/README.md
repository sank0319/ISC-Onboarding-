# `onboarding/scripts/`: the code

Everything that runs. Configuration lives in [`../config/`](../config/README.md) and [`../connectors/`](../connectors/README.md);
this folder holds only Python. Standard library plus `requests` and `PyYAML` (see `requirements.txt`).

## How the pieces fit
```
onboard.py ── secrets_store.py   credentials (HashiCorp Vault, or ISC_* environment variables)
    │
    ├──────── isc_client.py      every HTTP call to ISC (token, retry, paging, dry-run guard)
    │
    └──────── onboarder.py ───── connectors/<type>.py      one file per connector (see connectors/README.md)
                  │
                  └── uploader.py     only for feed uploads, and only when ISC_UPLOAD_JAR is set
```
`batch.py`, `validation.py`, `common.py` and `run_log.py` are helpers used by all of the above.
`simulator.py` replaces `isc_client.py` for `--offline` runs and for the tests. `discover.py` and
`connectivity_check.py` are separate tools started by their own workflows; `onboard.py` never calls them.

## File by file
| File | What it does | Called by |
|---|---|---|
| [`onboard.py`](onboard.py) | The entry point and CLI. Loads defaults, selects and validates apps, loads credentials, builds the client, runs each app, writes the report and the run log, and exits 1 if any app failed or was partial. | the *ISC Bulk Onboarding* and *Offline rehearsal* workflows, or you |
| [`onboarder.py`](onboarder.py) | `Onboarder`: the ISC operations every connector shares (find, create source, schema, correlation, aggregate, requestable entitlements, access profiles, applications, governance groups) and `run(app)`, which dispatches to the connector and keeps the batch going if one app fails. | `onboard.py`, connectors |
| [`isc_client.py`](isc_client.py) | `ISCClient`: OAuth client-credentials token (renewed shortly before it expires and once on a 401), retries (up to 10 attempts) on 429, 5xx and network errors, sends `X-SailPoint-Experimental: true` on every request, paging (`get_all`), JSON-Patch, and a global **dry-run switch that blocks every write**. | `onboard.py`, `discover.py`, `connectivity_check.py` |
| [`secrets_store.py`](secrets_store.py) | Gets the ISC credentials. In Actions: GitHub OIDC token, then Vault JWT login, then a KV v2 read, then token revoke. Falls back to `ISC_*` variables when `VAULT_ADDR` is unset. `--claims` prints the OIDC `iss`, `sub`, `aud`. Secret values never appear in errors or logs. | `onboard.py`, `discover.py`, `connectivity_check.py` |
| [`uploader.py`](uploader.py) | Wraps SailPoint's File Upload Utility JAR: stages the feed as `<sourceID>-<name>`, runs `java -jar`, passes credentials through `SAIL_CLIENT_ID` / `SAIL_CLIENT_SECRET` (never the command line), and returns redacted output. | `onboarder.aggregate()` |
| [`batch.py`](batch.py) | Loads app configs by glob, handles waves and the `{env}` placeholder, selects apps from an apps CSV, checks the folder layout, and runs the offline validation of the whole batch. | `onboard.py` |
| [`validation.py`](validation.py) | Offline checks shared by connectors: names, owners, CSV reading that never raises, access-profile entries, duplicate detection. | `batch.py`, connectors |
| [`common.py`](common.py) | Small helpers: YAML and CSV loading, `deep_merge`, `load_defaults` (global defaults plus every connector template), filter escaping, name normalising. | everything |
| [`run_log.py`](run_log.py) | JSON-lines run log with redaction (`redact`, `register_secret`). Logging never breaks a run: after the first write error it switches itself off. | `onboard.py`, `isc_client.py`, `onboarder.py` |
| [`simulator.py`](simulator.py) | `FakeISC`, an in-memory ISC with the same interface as `ISCClient` (required fields, unique names, filters, paging, injectable faults); `fill_placeholders` and `rehearsal_isc` for `--offline`. | tests, `onboard.py --offline` |
| [`discover.py`](discover.py) | Read-only lookups (GET only): connector script names, existing sources, one source's settings with credentials masked, and a ready template block. | the *ISC discover* workflow |
| [`connectivity_check.py`](connectivity_check.py) | Loads credentials, gets a token, lists 5 source names. | the *ISC connectivity test* workflow |
| [`_sourcetemplate.py`](_sourcetemplate.py) | **Stale duplicate.** Byte-for-byte identical to [`connectors/_sourcetemplate.py`](connectors/_sourcetemplate.py), which is the real base class. Nothing imports this copy. (missing: a decision to delete it, e.g. `git rm onboarding/scripts/_sourcetemplate.py`) | none |
| [`connectors/`](connectors/README.md) | One file per connector type. | `onboarder.py` |

## Command-line options (`onboard.py`)
| Option | Meaning |
|---|---|
| `--apps GLOB` | app config files (default `onboarding/connectors/*/apps/**/*.yaml`) |
| `--wave N` | only configs in `wave-N` folders; a wave with no configs is an error |
| `--apps-csv FILE` | choose and order apps by a CSV (`applicationName,applicationType`) |
| `--defaults FILE` | global defaults (default `onboarding/config/defaults.yaml`) |
| `--dry-run` | plan only; every write is blocked |
| `--validate-only` | offline checks, then stop |
| `--offline` | rehearse the whole run against the simulator: no tenant, no credentials, nothing sent |
| `--report FILE` | where the JSON summary goes (default `onboarding-report.json` in the repo root; ignored by `.gitignore`, but the file is still tracked in git: missing a `git rm --cached` commit) |

Exit code 1 means validation failed, or at least one app was `failed` or `partial`.

## Environment variables the code reads
| Variable | Read by | Meaning |
|---|---|---|
| `ISC_TARGET` | `onboard.py` | `production` selects `data/production/` feeds; anything else selects `sandbox` |
| `ISC_TENANT`, `ISC_CLIENT_ID`, `ISC_CLIENT_SECRET`, `ISC_BASE_URL` | `secrets_store.py` | credentials fallback (used only when `VAULT_ADDR` is unset); `ISC_BASE_URL` overrides the default `https://<tenant>.api.identitynow.com` and is needed for this tenant (`identitynow-demo.com`) |
| `VAULT_ADDR`, `VAULT_ROLE`, `VAULT_SECRET_PATH`, `VAULT_NAMESPACE`, `VAULT_AUTH_MOUNT`, `VAULT_KV_MOUNT`, `VAULT_AUDIENCE` | `secrets_store.py` | Vault mode; defaults: auth mount `jwt`, KV mount `secret`, audience `vault-isc-onboarding` |
| `ACTIONS_ID_TOKEN_REQUEST_URL`, `ACTIONS_ID_TOKEN_REQUEST_TOKEN` | `secrets_store.py` | set by GitHub when the job has `id-token: write` |
| `ISC_UPLOAD_JAR` | `onboarder.py` | path of the File Upload Utility JAR; when set, feeds go through it |
| `SAIL_CLIENT_ID`, `SAIL_CLIENT_SECRET` | the JAR | set by `uploader.py` for the child process only |
| `<NAME>` listed under a connector's `secretEnv` | `_sourcetemplate.py` | e.g. `JDBC_PASSWORD`; read only when a source is created |
| `ISC_LOG_DIR` | `onboard.py` | run-log folder (default `onboarding/logs`; the tests redirect it) |
| `GITHUB_ACTIONS` | `secrets_store.py` | must be `true`; Vault login and `--claims` refuse to run outside GitHub Actions |
| `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, `GITHUB_ACTOR`, `GITHUB_SHA`, `GITHUB_STEP_SUMMARY` | `run_log.py`, `onboard.py` | provided by GitHub; used in the log and the job summary |

## ISC endpoints the code calls
| Area | Endpoints | Where |
|---|---|---|
| Auth | `POST /oauth/token` (client credentials) | `isc_client.py` |
| Sources | `/v3/sources` (list, create), `/v3/sources/{id}/schemas` and `/v3/sources/{id}/schemas/{schemaId}`, `/beta/sources/{id}/correlation-config` (the HTTP method for each call is in `onboarder.py`) | `onboarder.py` |
| Accounts | `POST /v2024/sources/{id}/load-accounts` (the `/v3/` path returned 404 on this tenant), `GET /v3/accounts`, `GET /beta/task-status/{id}` | `onboarder.py` |
| Entitlements | `GET /beta/entitlements`, `POST /beta/entitlements/bulk-update`, `PUT /beta/entitlements/{id}/entitlement-request-config` | `onboarder.py` |
| Access profiles and apps | `/v3/access-profiles` and `/v3/access-profiles/{id}`, `/beta/source-apps`, `/beta/source-apps/{id}`, `/beta/source-apps/{id}/access-profiles`, `/beta/source-apps/all` | `onboarder.py` |
| Identities and groups | `GET /v3/public-identities`, `GET/POST /beta/workgroups` and `.../members`, `bulk-add`, `bulk-delete` | `onboarder.py` |
| Discovery | `GET /v3/connectors` (then `/beta/connectors`), `GET /v3/sources`, `GET /v3/sources/{id}/schemas` | `discover.py` |
| Vault | `POST /v1/auth/<mount>/login`, `GET /v1/<kv>/data/<path>`, `POST /v1/auth/token/revoke-self` | `secrets_store.py` |

Beta endpoints are used where there is no stable equivalent; their request bodies must be confirmed on the tenant.

## Rules for changing the code
- **No secrets in output.** Anything that may contain a secret goes through `redact()`; register new secret values with
  `register_secret()`. Do not print tokens, passwords or request bodies.
- **Offline first.** Validation must not make API calls. Anything that writes goes through `ISCClient.request(..., write=True)`
  so the dry-run guard covers it.
- **Add tests** next to the existing ones in [`tests/`](../../tests/README.md); every connector and helper has them.
- Add a connector by following [`connectors/README.md`](connectors/README.md).

## Where to look
Design decisions: [`docs/structure.md`](../../docs/structure.md). Status: [`docs/status.md`](../../docs/status.md).
The repo overview: [`README.md`](../../README.md).
