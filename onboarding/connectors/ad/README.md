# Active Directory connector (`ad`)

Does **not** create a source: the AD source already exists in ISC. For each in-scope AD group in an export file it builds
the approval chain and the access profile, then attaches the profiles to an application.

| | |
|---|---|
| `connectorType` | `ad` |
| ISC source | an existing AD source, named in `ad.sourceName` |
| Code | [`onboarding/scripts/connectors/ad.py`](../../scripts/connectors/ad.py) |
| Settings | [`ad-template.yaml`](ad-template.yaml) (the `ad:` block) |
| Feed file | the group export, `data/<env>/<app name>-group.csv` |
| Status | code and tests exist; not run against a real AD source (missing: an AD source and a group export on the tenant to test against) |

## What is in this folder
| Path | What it is | Rule |
|---|---|---|
| `ad-template.yaml` | Default AD settings: which columns hold the group value, name and approvers, the access profile name template, governance-group owner and name limit. | An app can override most keys under its own `ad:` block. |
| `apps/` | App YAMLs placed directly in this folder (AD has no waves), see [`apps/README.md`](apps/README.md). | `connectorType: ad`. |
| `data/` | Group exports, see [`data/README.md`](data/README.md). | File name `<app name>-group.csv`. |

## How it works
For every row of the export (optionally filtered by `ad.scopeRegex` on the value column):

1. Match the row's `valueColumn` (default `distinguishedName`) to an **entitlement** already aggregated from the AD source.
   Rows with no entitlement are listed as "not found" (aggregate AD first).
2. Read the **approvers** from the approval attribute (default `extensionAttribute10`, written `<<a@x.com; b@x.com>>`)
   and resolve each e-mail to exactly one ISC identity. If any cannot be resolved, that access profile is skipped.
3. Create or reuse a **governance group** named after the access profile, owned by `governanceGroupOwner` (or the
   application owner when empty), with the approvers as members. An existing group is reused and missing approvers are
   added; with `syncRemoveGovernanceGroupMembers: true` members no longer listed are removed too.
4. Create or reuse the **access profile** with approvals Level 1 = manager, Level 2 = that governance group.
5. Create or reuse the **application** and attach all the profiles.

The run status is `partial` when anything was skipped or missing; the report lists each skipped profile.

## App YAML reference
```yaml
apps:
  - appName: Treasury
    connectorType: ad
    owner: Sanson.Seah
    application: Treasury                # required: the ISC application to attach profiles to
    ad:
      sourceName: Corp AD                # required: existing AD source in ISC
      entitlementExport: onboarding/connectors/ad/data/{env}/Treasury-group.csv   # required
      valueColumn: distinguishedName     # optional (defaults from ad-template.yaml)
      nameColumn: cn
      approvalAttribute: extensionAttribute10
      accessProfileNameTemplate: "{app} - {name}"   # must contain {name}
      scopeRegex: "OU=Treasury"          # optional filter on the value column
      delimiter: ","                     # optional
      governanceGroupOwner: ""           # optional alias or e-mail
```
Export columns: the value column, the name column, the approval attribute and optionally `description`. An example is
[`docs/examples/feeds/Treasury-group.csv`](../../../docs/examples/feeds/Treasury-group.csv).

## Template settings (`ad-template.yaml`)
`valueColumn`, `nameColumn`, `approvalAttribute`, `accessProfileNameTemplate` (`"{app} - {name}"`), `governanceGroupOwner`,
`governanceGroupNameMaxLength` (128, to be verified on the tenant) and `syncRemoveGovernanceGroupMembers` (false).

## Checks before anything is sent
`ad.sourceName` or `ad.entitlementExport` missing; `application` missing; an invalid name template or `scopeRegex`;
export unreadable or empty; the value or approval column not in the header; any row with an empty value or an empty or
invalid approver e-mail ("level 2 approver is mandatory"). Violations stop the run before any API call.

## ISC API calls made
`GET /v3/sources`, `GET /beta/entitlements`, `GET /v3/public-identities`, `GET/POST /beta/workgroups` and
`/beta/workgroups/{id}/members` (`bulk-add`, `bulk-delete`), `GET/POST/PATCH /v3/access-profiles`,
`GET/POST/PATCH /beta/source-apps`.

## Not yet confirmed
The governance-group name limit, the exact workgroup request bodies, and the behaviour when a requester's manager is
also in the governance group (self-approval) must be checked on the tenant (the main README's "Verify in sandbox first").

## Run and test
- Offline: `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/ad/apps/**/*.yaml*"`
- Tests: [`tests/test_access_profiles.py`](../../../tests/test_access_profiles.py) (governance-group flow),
  [`tests/test_validation_files.py`](../../../tests/test_validation_files.py) (export checks).

## Where to look
[`ad.py`](../../scripts/connectors/ad.py) (the whole flow, `parse_approvers`), [`onboarder.py`](../../scripts/onboarder.py)
(`ensure_governance_group`, `ensure_access_profile`, `ensure_source_app`).
