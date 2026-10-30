# ISC Bulk Onboarding (GitHub Actions)

## Setup
1. GitHub environments: `sandbox-readonly`, `sandbox`, `production` (add required reviewers on `production`).
2. Secrets per environment: `ISC_TENANT`, `ISC_CLIENT_ID`, `ISC_CLIENT_SECRET` (client-credentials API client).
3. In `onboarding/config/defaults.yaml`: set the SQL Loader connector + connectorAttributes (copy from a working source), `clusterId`, and the AD extension attribute name.

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

Do not commit production account files containing personal data; fetch them from secure storage in the workflow instead.

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
