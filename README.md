# ISC Bulk Onboarding (GitHub Actions)

A CI/CD pipeline that creates applications in **SailPoint Identity Security Cloud (ISC)** from reviewed YAML configs and
feed files: source, schema, correlation, account load, requestable entitlements, access profiles, application. It runs
from GitHub Actions, gets its credentials from HashiCorp Vault, and has an offline mode that needs no tenant.

Status snapshot (dated, with what is verified and what is not): [`docs/status.md`](docs/status.md).

## Contents
- [What is in this repository](#what-is-in-this-repository) (connectors, workflows, scripts, external systems and APIs, variables, planned work)
- [Where the documentation is](#where-the-documentation-is) (every README and what it covers)
- [Setup](#setup), [Credentials (HashiCorp Vault)](#credentials-hashicorp-vault), [Flow](#flow)
- [Per type](#per-type), [AD extension attribute format](#ad-extension-attribute-format), [Verify in sandbox first](#verify-in-sandbox-first)
- [Repository layout](#repository-layout), [Running a wave](#running-a-wave), [Choosing apps with a CSV](#choosing-apps-with-a-csv)
- [Adding a connector](#adding-a-connector), [Working without a tenant](#working-without-a-tenant), [Running the tests](#running-the-tests), [Logs](#logs)

## What is in this repository
"Verified" means seen working on the tenant. "Built" means written and passing the offline tests and rehearsal only.
"Planned" means not started.

### Connectors
| `connectorType` | What it creates | Feed | Cluster | Status | Docs |
|---|---|---|---|---|---|
| `delimited` | Delimited File source from a CSV | CSV uploaded | no | **Verified** (File Upload Utility upload, run #31) | [README](onboarding/connectors/delimited/README.md) |
| `sqlloader` | Delimited File source from a predefined SQL Loader template | CSV uploaded | no | Built | [README](onboarding/connectors/sqlloader/README.md) |
| `ad` | Governance groups, approvers and access profiles on an existing AD source | group export | n/a | Built (missing: an AD source and export on the tenant to test against) | [README](onboarding/connectors/ad/README.md) |
| `jdbc` | JDBC source on a virtual appliance | none | yes | Built; needs script name and cluster ID | [README](onboarding/connectors/jdbc/README.md) |
| `api` | Web-services source | none | normally no | Built; needs script name | [README](onboarding/connectors/api/README.md) |
| `windows` | Windows source on a virtual appliance | none | yes | Built; needs script name and cluster ID | [README](onboarding/connectors/windows/README.md) |
| `linux` | Linux (SSH) source on a virtual appliance | none | yes | Built; needs script name and cluster ID | [README](onboarding/connectors/linux/README.md) |
| "File Based" (schedule 1-7 Dec) | possibly the same as Delimited File | | | Planned (missing: confirmation from the coworker of what it means) | |

### Workflows ([`.github/workflows/`](.github/workflows/README.md))
| Workflow file | Purpose | Status |
|---|---|---|
| `isc-onboarding.yml` | validate; dry-run plan on pull requests; manual apply (sandbox or production) with optional File Upload Utility | Verified on sandbox (missing: the run numbers or dates of those runs) |
| `isc-connectivity-test.yml` | Vault login, token, list up to 5 sources | Verified (missing: the date of the passing run; see [Credentials](#credentials-hashicorp-vault)) |
| `isc-discover.yml` | read-only lookup of connector script names and a working source's settings | Verified (found the SQL Loader and Delimited File connector names) |
| `isc-rehearsal.yml` | tests plus offline rehearsal of every config, no tenant, no credentials | Built (missing: a first run on GitHub; it has only been run locally) |

### Scripts ([`onboarding/scripts/`](onboarding/scripts/README.md))
| File | Purpose |
|---|---|
| `onboard.py` | CLI entry point: select, validate, run, report |
| `onboarder.py` | shared ISC operations and the per-app runner (`Onboarder`) |
| `isc_client.py` | ISC REST client: OAuth, retry, paging, dry-run guard |
| `secrets_store.py` | credentials from Vault (GitHub OIDC) or `ISC_*` variables |
| `uploader.py` | SailPoint File Upload Utility wrapper |
| `batch.py`, `validation.py`, `common.py` | batch loading and selection, offline validation, shared helpers |
| `run_log.py` | JSON-lines run log with redaction |
| `simulator.py` | in-memory ISC for tests and `--offline` |
| `discover.py`, `connectivity_check.py` | read-only discovery and the connectivity test |
| `_sourcetemplate.py` (in `onboarding/scripts/`) | an exact copy of `connectors/_sourcetemplate.py`; nothing imports it (missing: a decision to delete it, see [scripts README](onboarding/scripts/README.md)) |
| `connectors/` | one file per connector, plus the registry (`__init__.py`), the starter (`_template.py`) and the shared base (`_sourcetemplate.py`) ([guide](onboarding/scripts/connectors/README.md)) |

### External systems, APIs and tools
Everything the pipeline depends on, including items not yet in use.

| Item | Used for | Where it is used | Status |
|---|---|---|---|
| SailPoint ISC REST API, `/v3`, `/beta`, `/v2024` (one call: `load-accounts`) and `/oauth/token` | everything the pipeline writes and reads | `isc_client.py`, `onboarder.py`, `discover.py`; full endpoint list in the [scripts README](onboarding/scripts/README.md#isc-endpoints-the-code-calls) | Verified for sources, accounts and discovery; the `/beta` request bodies are still to be confirmed |
| ISC virtual appliance clusters | JDBC, Windows and Linux sources | `clusterId` in the connector templates and `onboarding/config/defaults.yaml` | Planned (cluster ID not known yet) |
| HashiCorp Vault: JWT auth, KV v2, token revoke (`/v1/auth/<mount>/login`, `/v1/<kv>/data/<path>`, `/v1/auth/token/revoke-self`) | ISC credentials, no secret stored in GitHub | `secrets_store.py` | Built and tested offline; (missing: a successful connectivity-test run on the real Vault; the section below says it is not yet tested) |
| HCP Vault Dedicated or self-hosted Vault | the Vault server itself | Vault side, see [Credentials](#credentials-hashicorp-vault) | Production role not created (missing: the production role and path) |
| GitHub Actions OIDC token | the identity Vault trusts | `secrets_store.py`, every job with `id-token: write` | Verified (missing: a `sub` format that is confirmed for environment jobs; see the note in [Credentials](#credentials-hashicorp-vault)) |
| GitHub Actions and its actions: `actions/checkout@v4`, `actions/setup-python@v5` (Python 3.11), `actions/setup-java@v4` (Temurin 17), `actions/upload-artifact@v4` | running the workflows, the Java runtime, uploading reports and logs | `.github/workflows/*.yml` | Used by the workflows (missing: confirmation that setup-java and the upload steps have passed on GitHub) |
| SailPoint File Upload Utility 4.1.0 (Java JAR, run with `java -jar`) | feed upload for Delimited File and SQL Loader sources | `uploader.py`, `onboarder.py` (only when `ISC_UPLOAD_JAR` is set) | Verified (the connector README records run #31; the JAR download is checksum-checked) |
| `sha256sum`, `curl` | verifying and fetching the File Upload Utility in the apply job | `.github/workflows/isc-onboarding.yml` | Used by the apply job (missing: a logged run of the download step) |
| Python 3.11 in CI | running the code and the tests in workflows | all workflows | Used by all workflows (missing: a pytest run on Python 3.11; the tests were run locally on Python 3.9 only) |
| Python libraries `requests`, `PyYAML` (runtime) | HTTP calls, reading YAML | `requirements.txt` | Verified |
| `pytest` (tests only) | the offline test suite | `requirements-dev.txt`, `tests/` | Verified (858 tests pass locally) |
| `git` and the `github-actions[bot]` identity | committing run logs back to the branch | `isc-onboarding.yml` (apply job) | Verified (missing: confirmation that branch protection allows the bot to push) |
| `jq` | reading run logs (optional) | by hand, see [Logs](#logs) | optional |

### Variables and secrets
Per GitHub environment. Full table and rules: [workflows README](.github/workflows/README.md#github-environments-variables-and-secrets).
`VAULT_ADDR`, `VAULT_ROLE`, `VAULT_SECRET_PATH`, `VAULT_NAMESPACE`, `VAULT_AUTH_MOUNT`, `VAULT_KV_MOUNT`, `ISC_BASE_URL`,
`FILE_UPLOAD_JAR_URL`, `FILE_UPLOAD_JAR_SHA256` (variables); `JDBC_PASSWORD` (secret); `ISC_TENANT`, `ISC_CLIENT_ID`,
`ISC_CLIENT_SECRET` (legacy secrets, to be deleted). Environment variables the code reads: [scripts README](onboarding/scripts/README.md#environment-variables-the-code-reads).

Planned, not yet wired into the workflows: `API_TOKEN`, `WINDOWS_PASSWORD`, `LINUX_PASSWORD` (each connector README says how to add them).

### Planned or not done yet
- Run the SQL Loader, JDBC, API, Windows, Linux and AD connectors on the tenant (it has been down since about 10 Nov).
- Real connector script names, attribute names and cluster IDs for JDBC, API, Windows and Linux (use the discover workflow).
- Decide whether the pipeline writes the schema for no-feed connectors or ISC discovers it.
- Where database and API secrets live (GitHub secret stopgap versus Vault); wire `API_TOKEN`, `WINDOWS_PASSWORD`, `LINUX_PASSWORD` into the workflow if used.
- Production: Vault role and path, `data/production/` feeds, required reviewers.
- Housekeeping: delete the legacy `ISC_*` secrets, make the repository private, delete the HCP cluster when finished.
- Stop tracking `onboarding-report.json` and `.DS_Store` files in git (they are listed in the `.gitignore` now but still tracked; missing: a decision and a `git rm --cached` commit).
- Confirm what "File Based" means in ISC; confirm the real SQL Loader feed columns.

## Where the documentation is
Every directory that holds code, configuration, data or docs has a README that says what it is for, what each file does,
what may and may not go in it, and where to look for more. `tests/test_docs.py` fails if a directory loses its README,
if a connector README stops naming its template and code file, or if a relative link breaks, so update the docs in the
same change as the code.

| Directory or file | README (purpose and rules) |
|---|---|
| repository root | this file |
| `docs/` | [docs index](docs/README.md); [status](docs/status.md), [structure](docs/structure.md), [validation evidence](docs/validation-evidence.md) |
| `docs/examples/` | [examples](docs/examples/README.md); [feeds](docs/examples/feeds/README.md) |
| `docs/meeting-notes/` | [meeting notes](docs/meeting-notes/README.md) |
| `.github/workflows/` | [workflows](.github/workflows/README.md) |
| `onboarding/` | [onboarding](onboarding/README.md) |
| `onboarding/config/` | [config](onboarding/config/README.md) |
| `onboarding/connectors/` | [connectors index](onboarding/connectors/README.md) |
| `onboarding/connectors/<type>/` (one per connector) | `README.md` in each: [delimited](onboarding/connectors/delimited/README.md), [sqlloader](onboarding/connectors/sqlloader/README.md), [ad](onboarding/connectors/ad/README.md), [jdbc](onboarding/connectors/jdbc/README.md), [api](onboarding/connectors/api/README.md), [windows](onboarding/connectors/windows/README.md), [linux](onboarding/connectors/linux/README.md) |
| `onboarding/connectors/<type>/apps/` | `README.md` in each: [delimited](onboarding/connectors/delimited/apps/README.md), [sqlloader](onboarding/connectors/sqlloader/apps/README.md), [ad](onboarding/connectors/ad/apps/README.md), [jdbc](onboarding/connectors/jdbc/apps/README.md), [api](onboarding/connectors/api/apps/README.md), [windows](onboarding/connectors/windows/apps/README.md), [linux](onboarding/connectors/linux/apps/README.md) |
| `onboarding/connectors/<type>/apps/wave-1/` and `wave-2/` | `README.md` in each of the 12 wave folders (delimited, sqlloader, jdbc, api, windows, linux) |
| `onboarding/connectors/<type>/data/` | `README.md` in each: [delimited](onboarding/connectors/delimited/data/README.md), [sqlloader](onboarding/connectors/sqlloader/data/README.md), [ad](onboarding/connectors/ad/data/README.md), [jdbc](onboarding/connectors/jdbc/data/README.md), [api](onboarding/connectors/api/data/README.md), [windows](onboarding/connectors/windows/data/README.md), [linux](onboarding/connectors/linux/data/README.md) |
| `onboarding/connectors/{delimited,sqlloader,ad}/data/sandbox/` and `data/production/` | `README.md` in each of the six folders |
| `onboarding/scripts/` | [scripts](onboarding/scripts/README.md) |
| `onboarding/scripts/connectors/` | [connector code and how to add one](onboarding/scripts/connectors/README.md) |
| `onboarding/logs/` | [logs](onboarding/logs/README.md); the date folders under it are written by the pipeline and described there |
| `tests/` | [tests](tests/README.md) |

Connector-specific details (keys, checks, ISC calls, how to turn a connector on) are in each connector's own README, not here.

## Setup
1. GitHub environments: `sandbox-readonly`, `sandbox`, `production` (add required reviewers on `production`).
2. ISC credentials come from HashiCorp Vault, not GitHub secrets - see [Credentials (HashiCorp Vault)](#credentials-hashicorp-vault).
3. Per connector, fill the template `onboarding/connectors/<type>/<type>-template.yaml` (connector script name, attributes, cluster ID where one is needed; copy them from a working source with the *ISC discover* workflow). Global settings are in `onboarding/config/defaults.yaml`. Each connector's README says exactly what to set.
4. Python: `pip install -r requirements.txt -r requirements-dev.txt` (the workflows install the same files).

## Credentials (HashiCorp Vault)

The ISC API client id/secret are read from [HashiCorp Vault](https://www.hashicorp.com/en/products/vault) at run time; nothing secret is stored in GitHub.

1. The job requests a short-lived GitHub OIDC token (audience `vault-isc-onboarding`, see `DEFAULT_AUDIENCE` in `secrets_store.py`).
2. Vault's JWT auth method verifies it against GitHub, matches the role's `bound_claims`, and returns a short-lived client token.
3. The script reads one KV v2 secret (`ISC_TENANT`, `ISC_CLIENT_ID`, `ISC_CLIENT_SECRET`, optional `ISC_BASE_URL`), then revokes the token.

If `VAULT_ADDR` is unset, the `ISC_*` environment variables are used instead: the workflows still pass the `ISC_TENANT`, `ISC_CLIENT_ID`, `ISC_CLIENT_SECRET` environment secrets and the `ISC_BASE_URL` variable for this, so the pipeline keeps working until Vault is ready. If `VAULT_ADDR` is set, `ISC_*` variables are ignored; delete those GitHub secrets once a Vault run has passed.

### Vault side (once per GitHub environment)
Use one secret path, policy and role per environment (`sandbox-readonly`, `sandbox`, `production`). On HCP Vault Dedicated also `export VAULT_NAMESPACE=admin`.
```bash
export VAULT_ADDR=https://<your-vault>:8200

vault secrets enable -path=secret -version=2 kv          # skip if a KV v2 mount already exists
vault kv put secret/isc/sandbox \
  ISC_TENANT=<tenant> ISC_CLIENT_ID=<id> ISC_CLIENT_SECRET=<secret>   # add ISC_BASE_URL=... only if needed

# policy: read on exactly this one secret, nothing else
vault policy write isc-sandbox - <<'POLICY'
path "secret/data/isc/sandbox" { capabilities = ["read"] }
POLICY

vault auth enable jwt
vault write auth/jwt/config \
  oidc_discovery_url=https://token.actions.githubusercontent.com \
  bound_issuer=https://token.actions.githubusercontent.com

# <SUB> = the exact value printed by the connectivity test (next section); do not hand-write it
vault write auth/jwt/role/isc-sandbox \
  role_type=jwt \
  user_claim=repository \
  bound_audiences=vault-isc-onboarding \
  bound_claims='{"sub":"<SUB>"}' \
  token_policies=isc-sandbox \
  token_ttl=5m token_max_ttl=10m
```
`secret/data/...` is the KV v2 API path for `secret/isc/sandbox`. Pinning `sub` per GitHub environment keeps the roles apart; the `production` role must not accept the `sandbox` subject.

### GitHub side
Per environment (Settings > Environments > Variables, not secrets): `VAULT_ADDR`, `VAULT_ROLE` (e.g. `isc-sandbox`), `VAULT_SECRET_PATH` (e.g. `isc/sandbox`), `VAULT_NAMESPACE` (`admin` on HCP Vault Dedicated, empty otherwise); optional `VAULT_AUTH_MOUNT` (default `jwt`), `VAULT_KV_MOUNT` (default `secret`). `VAULT_AUDIENCE` (default above, must equal `bound_audiences`) is not wired into the workflows; add it to their `env:` if you change it. The jobs already have `permissions: id-token: write`. The old `ISC_*` secrets and the `ISC_BASE_URL` variable can be deleted (`ISC_BASE_URL` is now an optional key in the Vault secret).

### Connectivity test and finding the `sub`
Actions > "ISC connectivity test" > Run workflow, pick the environment. The first step prints `iss`, `sub` and `aud` of that job's OIDC token (not secret); copy `sub` into the role's `bound_claims`. Do not guess the value. GitHub's changelog (dated 23 April 2026) says that repositories created after 15 July 2026, and renamed or transferred ones, use the immutable claim format with numeric IDs, and that older repositories are unchanged unless they opt in ([changelog](https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-GitHub-actions-oidc-tokens/)). The changelog's example is for a branch, not an environment; (missing: the confirmed `sub` template for an environment-scoped job, which is in GitHub's OIDC documentation and was not checked here). The second step logs in, reads the secret, gets an ISC token and prints up to 5 source names. `secrets_store.py --claims` only works inside Actions.

### Notes
- Vault must be reachable from GitHub-hosted runners (public HTTPS endpoint) or you need a self-hosted runner inside your network. `VAULT_ADDR` must be `https://` (plain `http://` only for `localhost`/`127.0.0.1`).
- Getting a Vault (pricing, limits and any free tier are not verified here; check HashiCorp's [pricing page](https://www.hashicorp.com/products/vault/pricing)):
  - HCP Vault Dedicated, run by HashiCorp: top-level namespace is `admin`; the docs list Development, Essentials and Standard tiers ([tiers](https://developer.hashicorp.com/hcp/docs/vault/tiers-and-features)); sign-up is linked from the [product page](https://www.hashicorp.com/en/products/vault). Check the HCP docs on public network access to confirm your cluster has a public endpoint.
  - Self-hosted Vault Community Edition ([install](https://developer.hashicorp.com/vault/install)): you run, patch and secure it yourself, and it needs a public TLS endpoint for hosted runners. Namespaces are an Enterprise feature, so leave `VAULT_NAMESPACE` empty.
  - Do not use `vault server -dev` for real credentials (in-memory, throwaway).
- Vault status: (missing: the result of a connectivity-test run against the real Vault. Until one passes, Vault is built and tested offline only.)

## Flow
PR with app configs -> validate + sandbox dry-run plan -> merge -> Run workflow (sandbox, dry_run=false) -> verify -> Run workflow (production).

## Per type
| Type | Actions |
|---|---|
| sqlloader | Create a Delimited File source from the SQL Loader template -> schema -> correlation -> upload file + aggregate -> entitlements requestable (L1 Manager -> L2 Source Owner) |
| delimited | Create source -> schema (from file header) -> correlation -> upload file + aggregate -> entitlements requestable (L1 Manager -> L2 Source Owner) |
| jdbc, api, windows, linux | Create source from the template (secrets from environment variables) -> schema -> correlation -> aggregate (no file) -> entitlements requestable (L1 Manager -> L2 Source Owner) |
| ad | No source. Per in-scope group in the export: governance group (named after the AP) with approvers from the extension attribute -> access profile (L1 Manager -> L2 governance group) -> attach to application |

Each type is described in full in its own README under [`onboarding/connectors/`](onboarding/connectors/README.md).

## AD extension attribute format
`<<approver1@client.com; approver2@client.com>>` (column set in `ad.approvalAttribute`).
- Empty attribute or invalid email -> validation fails before any API call.
- Approver email not found / ambiguous in ISC -> that access profile is skipped and listed in the report (run status `partial`).
- Existing governance group -> reused; missing approvers added. Set `syncRemoveGovernanceGroupMembers: true` to also remove members no longer in the attribute.
- Governance group owner = `ad.governanceGroupOwner`, or the app owner if empty.

## Verify in sandbox first
- `/beta/sources/{id}/correlation-config` payload (attributeAssignments) for your tenant version.
- Connector script names (`GET /v3/connectors`).
- Beta endpoints used: entitlements bulk-update, entitlement-request-config, source-apps, task-status, workgroups (create, members, members/bulk-add, members/bulk-delete) - confirm request body shapes.
- Governance group name length limit on your tenant.
- Self-approval behaviour when the requester's manager is also in the governance group.

Production account files contain personal data: `onboarding/connectors/*/data/production/*.csv` is git-ignored by the root `.gitignore`; place those files there at run time from secure storage.

## Repository layout
```
.github/workflows/                 the four workflows (README inside)
.gitignore                         production feeds, generated report and caches
requirements.txt                   runtime libraries (requests, PyYAML)
requirements-dev.txt               test libraries (pytest)
onboarding/
  config/defaults.yaml             global settings only (README inside)
  connectors/README.md             index of connector folders
  connectors/<type>/               one folder per connector type: delimited, sqlloader, ad, jdbc, api, windows, linux
    <type>-template.yaml           the connector's shared settings (script name, attributes, schema, correlation, secretEnv)
    README.md                      how it works, its keys and checks, how to turn it on
    apps/wave-1/<app>.yaml  wave-2/   app definitions: the YAMLs that create the applications (wave 1 = pilot, 2 = the rest); ad has no waves
    data/sandbox/<source name>.csv    feed files (committed, made-up data); AD group files are <app name>-group.csv
    data/production/                  same names; supplied at run time, *.csv not committed
  scripts/                         the code (README inside); connectors/ holds one file per connector
  logs/                            run logs written by the pipeline (README inside)
onboarding-report.json             the report of the last run (written by onboard.py; git-ignored, but currently tracked)
docs/                              status.md, structure.md, validation-evidence.md, examples/, meeting-notes/
tests/                             offline tests; test_docs.py guards the documentation
```
Feed paths in an app YAML use `{env}`, e.g. `accountFile: onboarding/connectors/delimited/data/{env}/Test-Delimited-App.csv`. `{env}` becomes `production` when `ISC_TARGET=production`, otherwise `sandbox`. Configs under `onboarding/connectors/` must sit in `<connectorType>/apps/` and read their feed from `<connectorType>/data/{env}/` with the feed naming; validation reports violations. JDBC, API, Windows and Linux have no feed, so their `data/` only holds a README. Background: [docs/structure.md](docs/structure.md). What each directory is for: [Where the documentation is](#where-the-documentation-is).

## Running a wave
```
python onboarding/scripts/onboard.py --wave 1            # pilot app(s) only (all connectors with a wave-1 folder)
python onboarding/scripts/onboard.py --wave 2 --apps "onboarding/connectors/delimited/apps/**/*.yaml"
```
In GitHub: Run workflow -> `wave` = `1` or `2` (empty = everything). Apps outside `wave-N` folders (e.g. AD) are skipped when a wave is chosen. A wave with no configs is an error.

## Choosing apps with a CSV
A CSV with columns `applicationName` (the `appName` in the YAML) and `applicationType` (the `connectorType`) selects which apps are created, in file order:
```
python onboarding/scripts/onboard.py --apps-csv docs/examples/apps.csv --dry-run
```
In GitHub: Run workflow -> `apps_csv` = path in the repo. Unknown or missing names, unknown or mismatching types, duplicates and bad headers are reported together and stop the run before any API call. It combines with `--wave` and `--apps`.

## Adding a connector
The full guide is [`onboarding/scripts/connectors/README.md`](onboarding/scripts/connectors/README.md). In short:
1. Copy `onboarding/scripts/connectors/_template.py` to `<type>.py` and set `type` (the `connectorType` value). For a source that ISC pulls itself, subclass `SourceTemplate` (in `connectors/_sourcetemplate.py`) instead (see `jdbc.py`, about five lines).
2. Implement `validate(app, defaults)` (offline checks, no API calls) and `handle(ob, app, result)` using the shared `Onboarder` steps (`ensure_source`, `apply_schema`, `apply_correlation`, `aggregate`, `finish_non_ad`, `ensure_access_profile`, `ensure_source_app`, ...). For file-fed connectors implement `feed(app)`.
3. Create `onboarding/connectors/<type>/` with `<type>-template.yaml`, `README.md`, `apps/wave-1`, `apps/wave-2` and `data/` (with `sandbox/` and `production/` if it has a feed); copy an existing connector folder, and give each new folder a README as the existing ones have.
4. Add tests next to the existing ones (negative validation cases, handler against `FakeISC`) and add the type to `tests/test_connector_registry.py`.
5. Add a row to the connector tables here and in [`onboarding/connectors/README.md`](onboarding/connectors/README.md).
6. Run `python -m pytest -q tests` and `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/*/apps/**/*.yaml*"`.

Nothing else changes: the file is discovered automatically (modules starting with `_` are skipped), a duplicate `type` raises an error at start-up, and the default `--apps` glob covers the new folder.

## Working without a tenant
`python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/*/apps/**/*.yaml*"` runs every app config and
inactive sample through the whole pipeline against an in-memory ISC simulator: no tenant, no credentials, nothing is sent.
Unset tenant values (`REPLACE_*`) are simulated. The *Offline rehearsal* workflow (`isc-rehearsal.yml`) runs it, with the tests, on every pull request that touches `onboarding/`, `tests/` or the requirements (missing: its first run on GitHub).

## Running the tests
The test suite runs fully offline against an in-memory fake of the ISC API (`tests/conftest.py`); no tenant, credentials or network are needed. It has 858 tests (checked locally on 19 November 2026 with Python 3.9; the count grows with each connector).

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q
```

## Logs
Every `onboard.py` run except `--validate-only` and `--offline` writes a JSON-lines log to `onboarding/logs/<UTC date>/<HHMMSS>Z-<run id>.jsonl` (override the folder with `ISC_LOG_DIR`; the tests do) (run id = `GITHUB_RUN_ID`, else a random id). The `apply` job commits new files to the branch it ran on (`chore(logs): onboarding run <id> [skip ci]`); `plan` (pull request) runs never push and only upload the folder as the `plan-logs` artifact. The `onboarding-logs-<target>` artifact is a fallback if the push fails.

One event per line. Always present: `ts` (UTC), `level`, `event`, `run`. When applicable: `app`, `step`, `message`, `dry_run` (true on every event of a dry run), and event specific fields (`id`, `name`, `count`, ...). HTTP problems (`api_retry`, `api_error`) add `method`, `path`, `status`, `tracking_id`, `body` (max 300 chars). Events the code writes: `run_start`, `secrets_loaded`, `app_start`, `source_created|source_reused`, `schema_applied`, `correlation_applied`, `aggregation_done`, `upload_dry_run`, `upload_utility_done`, `entitlements_requestable`, `entitlements_missing`, `no_entitlements`, `governance_group_created|governance_group_reused`, `approver_ambiguous`, `access_profile_created|access_profile_reused|access_profile_updated|access_profile_skipped|access_profile_lookup_failed`, `export_rows_missing`, `application_created|application_reused`, `access_profile_attached`, `dry_run_write`, `api_retry`, `api_error`, `app_done`, `app_failed` (with traceback), `access_profiles_failed`, `run_crashed`, `run_end` (counts per status and status per app).

Read a run, errors only: `jq -c 'select(.level=="ERROR" or .level=="WARNING")' onboarding/logs/2026-10-30/*.jsonl`

Retention: logs accumulate in git history. Prune old date folders periodically (e.g. keep 90 days) with a normal commit; use the artifact retention setting for the `plan-logs` artifacts.

Security: request bodies, headers and credentials are never logged, and every line passes through `redact()` (bearer/JWT tokens, `secret`/`token`/`password`-style values, the configured client id/secret, long opaque strings; e-mail addresses become `a***@domain`). Redaction is best effort: messages can still contain identity aliases, app and group names, so prefer a private repository.

### Required repository settings
- Settings > Actions > General > Workflow permissions: allow "Read and write permissions" (or leave read-only: the `apply` job requests `contents: write` itself, which only works if the org/repo does not restrict it).
- If `main` is protected (required PRs or status checks), `GITHUB_TOKEN` cannot push the log commit and the step only warns. Either allow the `github-actions` bot to bypass the rule, run `apply` from an unprotected branch, or keep the artifact only.
- Pushes made with `GITHUB_TOKEN` do not start new workflow runs; the commit message also carries `[skip ci]` and the pull_request trigger ignores `onboarding/logs/**`.
