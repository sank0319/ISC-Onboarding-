# `tests/`: automated checks

858 pytest tests as of 19 November 2026 (the count grows with every connector). All of them run **offline**: no tenant, no credentials, no network, no Vault. ISC is replaced by an
in-memory fake ([`onboarding/scripts/simulator.py`](../onboarding/scripts/simulator.py)) that enforces ISC-like rules
(required fields, unique names, filters, paging) and can inject faults at any step.

## Run them
```
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q tests          # the whole suite
python3 -m pytest -q tests/test_jdbc.py    # one file
```
The *Offline rehearsal* workflow runs them on every pull request.

## Shared setup: `conftest.py`
Puts `onboarding/scripts` on the import path and provides the fixtures: `defaults` (the real merged defaults, with test
connector names and a test cluster set), `isc` (a `FakeISC`), `ob` (an `Onboarder` on it), `csv_file`, `delim_app` and
`ad_app` (builders for app configs and feeds), `run_batch` / `run_one`, a fake clock so nothing really sleeps, a temporary
log folder, and a guard that clears any ambient Vault variables. It re-exports `FakeISC`, `Fault`, `Returns` and `parse_filter`.

## Files
| File | What it checks |
|---|---|
| `test_validation_names.py` | names, owners and batch-level duplicates; no API calls |
| `test_validation_files.py` | config sections, account files (BOM, blank or duplicate headers, missing columns), SQL Loader templates, AD exports |
| `test_batch_selection.py` | waves, apps-CSV selection, `{env}` feed separation, connector-folder layout rules |
| `test_repo_layout.py` | the checked-in connector folders: every config follows the layout and every path resolves; the sample files are valid once activated |
| `test_template_sources.py` | JDBC-style connectors (API, Windows, Linux) share one implementation and have the standard folder shape |
| `test_jdbc.py` | JDBC validation, secrets only from the environment, create and reuse flow |
| `test_connector_registry.py` | auto-discovery, duplicate registration, unknown types; the list of known types |
| `test_uploader.py` | feeds through the File Upload Utility: staging, redaction, failure reporting, no credentials on the command line |
| `test_offline.py` | `onboard.py --offline`, and per-connector template loading and merging |
| `test_dry_run_and_rerun.py` | dry run never writes; a second real run neither fails nor duplicates |
| `test_existing_resources.py` | names already in ISC: reuse when ours, refuse when not |
| `test_pipeline_failures.py` | API failures at every step: the app is failed or partial and the batch continues |
| `test_access_profiles.py` | access-profile inputs and the AD governance-group flow |
| `test_ordering.py` | chunking, paging, step order |
| `test_lookups.py` | filter escaping and owner resolution |
| `test_isc_client.py` | retry, re-authentication, errors and the dry-run guard of `ISCClient` |
| `test_secrets_store.py` | Vault login (GitHub OIDC, JWT, KV v2), env fallback and secret scrubbing |
| `test_run_log.py` | the run log and redaction |
| `test_discover.py` | `discover.py` is read-only and never prints credential-looking values |
| `test_main.py` | the CLI: validation gate, report, exit codes |
| `test_docs.py` | every code and connector directory has a README, and every relative link in the Markdown files resolves |

## Rules
- Tests must not touch the network or a real tenant. Use `FakeISC` and the fixtures.
- Add negative cases with every validation rule, and a handler run for every connector.
- When you add a connector, add its type to the known-types assertions in `test_connector_registry.py`.
- Some tests assert the layout of the repository itself; if you move a folder, they will tell you what else to update.

## Where to look
The code under test: [`onboarding/scripts/`](../onboarding/scripts/README.md). Connector behaviour:
[`onboarding/connectors/`](../onboarding/connectors/README.md).
