# `onboarding/logs/`: run logs

Written by the pipeline, not by hand. Every `onboard.py` run (except `--validate-only` and `--offline`) writes one
JSON-lines file here:

```
onboarding/logs/<UTC date>/<HHMMSS>Z-<run id>.jsonl
```
`<run id>` is `GITHUB_RUN_ID`, or a random id when run locally. The folder can be moved with `ISC_LOG_DIR`
(the tests do this so they never write here). Each `<UTC date>` folder is created by the run that writes into it; there is
no README in each date folder because the format is the same for all of them.

## Where the files come from
The manual `apply` job commits new files here with the message `chore(logs): onboarding run <id> [skip ci]`.
Pull-request `plan` runs never push; they only upload the folder as the `plan-logs` artifact. If the push fails
(for example branch protection) the `onboarding-logs-<target>` artifact is the fallback.

## Format
One JSON event per line. Always present: `ts` (UTC), `level`, `event`, `run`. When applicable: `app`, `step`, `message`,
`dry_run` (true on every event of a dry run) and event-specific fields (`id`, `name`, `count`, ...). HTTP problems add
`method`, `path`, `status`, `tracking_id`, `body` (max 300 characters).

Events the code writes (grep `run_log` callers in `onboarding/scripts/` for the exact fields):
- Run and secrets: `run_start`, `secrets_loaded`, `run_crashed`, `run_end`
- App: `app_start`, `app_done`, `app_failed`, `access_profiles_failed`
- Source and schema: `source_created`, `source_reused`, `schema_applied`, `correlation_applied`
- Accounts and upload: `aggregation_done`, `upload_dry_run`, `upload_utility_done`
- Entitlements: `entitlements_requestable`, `entitlements_missing`, `no_entitlements`
- AD: `governance_group_created`, `governance_group_reused`, `approver_ambiguous`, `export_rows_missing`
- Access profiles and application: `access_profile_created`, `access_profile_reused`, `access_profile_updated`, `access_profile_skipped`, `access_profile_lookup_failed`, `access_profile_attached`, `application_created`, `application_reused`
- Writes and HTTP: `dry_run_write`, `api_retry`, `api_error`

Read a run, errors only:
```
jq -c 'select(.level=="ERROR" or .level=="WARNING")' onboarding/logs/2026-10-30/*.jsonl
```

## Security
Request bodies, headers and credentials are never logged, and every line passes through `redact()` (tokens, secret-like
values, the configured client id and secret, long opaque strings; e-mail addresses are shortened). Redaction is best
effort: messages can still hold identity aliases and app or group names, so keep the repository private.

## Rules
- Do not edit or hand-write files here; they are an audit trail.
- Pruning: logs accumulate in git history. Delete old date folders occasionally (for example keep 90 days) in a normal commit.
- The folder is not part of any workflow trigger: pull-request paths exclude `onboarding/logs/**`, so log commits never start a run.

## Where to look
[`run_log.py`](../scripts/run_log.py) (the writer and `redact`), the main [README](../../README.md#logs),
[`tests/test_run_log.py`](../../tests/test_run_log.py).
