# Status snapshot: 2026-11-19

The schedule against reality. "Verified" means seen working on the tenant. "Built" means written and passing the offline
tests and the offline rehearsal only. "Planned" means not started. Update the date when you change this page.

## Connectors

| `connectorType` | Status | What is still missing |
|---|---|---|
| `delimited` | **Verified** (File Upload Utility upload, run #31) | none for the pilot |
| `sqlloader` | Built | confirm the real SQL Loader feed columns |
| `ad` | Built | confirm the governance-group name length limit and self-approval behaviour on the tenant |
| `jdbc` | Built | real connector script name and cluster ID |
| `api` | Built | real connector script name |
| `windows` | Built | real connector script name and cluster ID |
| `linux` | Built | real connector script name and cluster ID |

"File Based" (schedule 1-7 Dec): planned; it may be the same as Delimited File, to be confirmed with the coworker.

## Workflows

| Workflow | Status |
|---|---|
| `isc-onboarding.yml` | Verified on sandbox |
| `isc-connectivity-test.yml` | Verified |
| `isc-discover.yml` | Verified (found the SQL Loader and Delimited File connector names) |
| Offline rehearsal (`isc-rehearsal.yml`) | Built: runs the tests and the offline rehearsal, no credentials. Not yet run on GitHub |

## Tests

The offline suite (`python -m pytest -q tests`) needs no tenant, credentials or network. It is the check that the built
connectors and the documentation are consistent. Passing it means the code and docs agree with each other; it does not
mean an application has been created on a real tenant.

## Open items

- Run the SQL Loader, JDBC, API, Windows, Linux and AD connectors on the tenant. The tenant has been down since about 10 Nov.
- Real connector script names, attribute names and cluster IDs for JDBC, API, Windows and Linux (use the discover workflow).
- Decide whether the pipeline writes the schema for no-feed connectors or ISC discovers it.
- Where database and API secrets live (GitHub secret stopgap versus Vault).
- Production: Vault role and path, `data/production/` feeds, required reviewers.
- Housekeeping: delete the legacy `ISC_*` secrets, make the repository private, delete the HCP cluster when finished.
- Run the offline rehearsal workflow on GitHub once to confirm it passes there (it has only been run locally).

The evidence to attach per application is in [`validation-evidence.md`](validation-evidence.md).
