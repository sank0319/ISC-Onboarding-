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
