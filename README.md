# ISC Bulk Onboarding (GitHub Actions)

## Setup
1. GitHub environments: `sandbox-readonly`, `sandbox`, `production` (add required reviewers on `production`).
2. ISC credentials come from HashiCorp Vault, not GitHub secrets - see [Credentials (HashiCorp Vault)](#credentials-hashicorp-vault).
3. In `onboarding/config/defaults.yaml`: set the SQL Loader connector + connectorAttributes (copy from a working source), `clusterId`, and the AD extension attribute name.

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
Actions > "ISC connectivity test" > Run workflow, pick the environment. The first step prints `iss`, `sub` and `aud` of that job's OIDC token (not secret); copy `sub` into the role's `bound_claims`. It looks like `repo:<owner>/<repo>:environment:<name>` for older repositories, but repositories created after 15 July 2026 (and renamed/transferred ones, or those that opted in) use numeric ids, e.g. `repo:<owner>@<id>/<repo>@<id>:environment:<name>` ([GitHub changelog](https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-GitHub-actions-oidc-tokens/)), so do not guess it. The second step logs in, reads the secret, gets an ISC token and prints 5 source names. `secrets_store.py --claims` only works inside Actions.

### Notes
- Vault must be reachable from GitHub-hosted runners (public HTTPS endpoint) or you need a self-hosted runner inside your network. `VAULT_ADDR` must be `https://` (plain `http://` only for `localhost`/`127.0.0.1`).
- Getting a Vault (pricing, limits and any free tier are not verified here; check HashiCorp's [pricing page](https://www.hashicorp.com/products/vault/pricing)):
  - HCP Vault Dedicated, run by HashiCorp: top-level namespace is `admin`; the docs list Development, Essentials and Standard tiers ([tiers](https://developer.hashicorp.com/hcp/docs/vault/tiers-and-features)); sign-up is linked from the [product page](https://www.hashicorp.com/en/products/vault). Check the HCP docs on public network access to confirm your cluster has a public endpoint.
  - Self-hosted Vault Community Edition ([install](https://developer.hashicorp.com/vault/install)): you run, patch and secure it yourself, and it needs a public TLS endpoint for hosted runners. Namespaces are an Enterprise feature, so leave `VAULT_NAMESPACE` empty.
  - Do not use `vault server -dev` for real credentials (in-memory, throwaway).
- Not yet tested against a real Vault; the first connectivity-test run is the real check.

## Flow
PR with app configs -> validate + sandbox dry-run plan -> merge -> Run workflow (sandbox, dry_run=false) -> verify -> Run workflow (production).

## Per type
| Type | Actions |
|---|---|
| sqlloader | Create source from template -> schema -> correlation -> aggregate -> entitlements requestable (L1 Manager -> L2 Source Owner) |
| delimited | Create source -> schema (from file header) -> correlation -> upload file + aggregate -> entitlements requestable (L1 Manager -> L2 Source Owner) |
| ad | No source. Per in-scope group in the export: governance group (named after the AP) with approvers from the extension attribute -> access profile (L1 Manager -> L2 governance group) -> attach to application |

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

Do not commit production account files containing personal data: `onboarding/connectors/*/feeds/production/*.csv` is git-ignored; place those files there at run time from secure storage.

## Repository layout
```
onboarding/
  config/defaults.yaml
  connectors/<type>/                 one folder per connector type: configs + feed data together
    wave-1/<app>.yaml  wave-2/...    delimited and sqlloader run in waves (1 = one pilot app, 2 = the rest)
    feeds/sandbox/<source name>.csv  account feed; group files are <source name>-group.csv
    feeds/production/                same names; supplied at run time, *.csv not committed
  scripts/
    onboard.py                       CLI entrypoint
    onboarder.py  batch.py  common.py  validation.py   shared code
    connectors/{delimited,sqlloader,ad}.py, _template.py   one file per connector
  logs/                              bot-committed run logs
docs/  structure.md  validation-evidence.md  examples/  meeting-notes/
```
Feed paths in an app YAML use `{env}`, e.g. `accountFile: onboarding/connectors/delimited/feeds/{env}/Test-Delimited-App.csv`. `{env}` becomes `production` when `ISC_TARGET=production`, otherwise `sandbox`. Configs under `onboarding/connectors/` must sit in the folder of their `connectorType` and follow the feed naming; validation reports violations. Background: [docs/structure.md](docs/structure.md).

## Running a wave
```
python onboarding/scripts/onboard.py --wave 1            # pilot app(s) only (all connectors with a wave-1 folder)
python onboarding/scripts/onboard.py --wave 2 --apps "onboarding/connectors/delimited/**/*.yaml"
```
In GitHub: Run workflow -> `wave` = `1` or `2` (empty = everything). Apps outside `wave-N` folders (e.g. AD) are skipped when a wave is chosen. A wave with no configs is an error.

## Choosing apps with a CSV
A CSV with columns `applicationName` (the `appName` in the YAML) and `applicationType` (the `connectorType`) selects which apps are created, in file order:
```
python onboarding/scripts/onboard.py --apps-csv docs/examples/apps.csv --dry-run
```
In GitHub: Run workflow -> `apps_csv` = path in the repo. Unknown or missing names, unknown or mismatching types, duplicates and bad headers are reported together and stop the run before any API call. It combines with `--wave` and `--apps`.

## Adding a connector
1. Copy `onboarding/scripts/connectors/_template.py` to `<type>.py` and set `type` (the `connectorType` value).
2. Implement `validate(app, defaults)` (offline checks, rules stay in this file) and `handle(ob, app, result)` using the shared `Onboarder` steps (`ensure_source`, `apply_schema`, `apply_correlation`, `aggregate`, `finish_non_ad`, `ensure_access_profile`, `ensure_source_app`, ...). For file-fed connectors implement `feed(app)`.
3. Create `onboarding/connectors/<type>/` with the app YAMLs (`wave-N/` if it runs in waves) and `feeds/sandbox/`, `feeds/production/`; add any defaults to `defaults.yaml`.
4. Add tests next to the existing ones (negative validation cases, handler against `FakeISC`).
5. Run `python -m pytest -q` and `python onboarding/scripts/onboard.py --validate-only`.

Nothing else changes: the file is discovered automatically (modules starting with `_` are skipped), a duplicate `type` raises an error at start-up, and the default `--apps` glob covers the new folder.

## Running the tests
The negative-test suite runs fully offline against an in-memory fake of the ISC API (`tests/conftest.py`); no tenant, credentials or network are needed.

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q
```

## Logs
Every `onboard.py` run (not `--validate-only`) writes a JSON-lines log to `onboarding/logs/<UTC date>/<HHMMSS>Z-<run id>.jsonl` (override the folder with `ISC_LOG_DIR`; the tests do) (run id = `GITHUB_RUN_ID`, else a random id). The `apply` job commits new files to the branch it ran on (`chore(logs): onboarding run <id> [skip ci]`); `plan` (pull request) runs never push and only upload the folder as the `plan-logs` artifact. The `onboarding-logs-<target>` artifact is a fallback if the push fails.

One event per line. Always present: `ts` (UTC), `level`, `event`, `run`. When applicable: `app`, `step`, `message`, `dry_run` (true on every event of a dry run), and event specific fields (`id`, `name`, `count`, ...). HTTP problems (`api_retry`, `api_error`) add `method`, `path`, `status`, `tracking_id`, `body` (max 300 chars). Events: `run_start` (attempt, actor, target, sha), `app_start`, `source_created|reused`, `schema_applied`, `correlation_applied`, `aggregation_done`, `entitlements_requestable`, `governance_group_created|reused`, `access_profile_created|reused|updated|skipped`, `application_created|reused`, `access_profile_attached`, `dry_run_write`, `api_retry`, `api_error`, `app_done`, `app_failed` (with traceback), `run_crashed`, `run_end` (counts per status and status per app).

Read a run, errors only: `jq -c 'select(.level=="ERROR" or .level=="WARNING")' onboarding/logs/2026-10-30/*.jsonl`

Retention: logs accumulate in git history. Prune old date folders periodically (e.g. keep 90 days) with a normal commit; use the artifact retention setting for the `plan-logs` artifacts.

Security: request bodies, headers and credentials are never logged, and every line passes through `redact()` (bearer/JWT tokens, `secret`/`token`/`password`-style values, the configured client id/secret, long opaque strings; e-mail addresses become `a***@domain`). Redaction is best effort: messages can still contain identity aliases, app and group names, so prefer a private repository.

### Required repository settings
- Settings > Actions > General > Workflow permissions: allow "Read and write permissions" (or leave read-only: the `apply` job requests `contents: write` itself, which only works if the org/repo does not restrict it).
- If `main` is protected (required PRs or status checks), `GITHUB_TOKEN` cannot push the log commit and the step only warns. Either allow the `github-actions` bot to bypass the rule, run `apply` from an unprotected branch, or keep the artifact only.
- Pushes made with `GITHUB_TOKEN` do not start new workflow runs; the commit message also carries `[skip ci]` and the pull_request trigger ignores `onboarding/logs/**`.
