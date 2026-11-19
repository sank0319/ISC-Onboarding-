# `docs/examples/`: worked inputs

Examples of the inputs the pipeline accepts. They sit outside `onboarding/connectors/` on purpose, so the default run
(`onboarding/connectors/*/apps/**/*.yaml`) never picks them up by accident. All data is made up.

| File | What it shows | How to try it |
|---|---|---|
| [`batch-01.yaml`](batch-01.yaml) | One file holding several apps in an `apps:` list (a SQL Loader app, a Delimited File app and an AD app). Its feed paths point into `docs/examples/feeds/`, so it is outside the connector layout rules by design. | `python onboarding/scripts/onboard.py --apps docs/examples/batch-01.yaml --validate-only` |
| [`apps.csv`](apps.csv) | The apps-CSV selection format: columns `applicationName` (the `appName` in the YAML) and `applicationType` (the `connectorType`). The run creates exactly these apps, in this order. | `python onboarding/scripts/onboard.py --apps-csv docs/examples/apps.csv --dry-run` |
| [`feeds/Claims-Portal.csv`](feeds/Claims-Portal.csv) | A SQL Loader style account feed: `USER_ID, USER_NAME, EMAIL, STATUS, ROLE`. | used by `batch-01.yaml` |
| [`feeds/Finance-Portal.csv`](feeds/Finance-Portal.csv) | A Delimited File account feed. | used by `batch-01.yaml` |
| [`feeds/Treasury-group.csv`](feeds/Treasury-group.csv) | An AD group export: `cn, distinguishedName, description, extensionAttribute10` (approvers written `<<a@x.com; b@x.com>>`). | the format the `ad` connector reads |

## Rules
- Examples are checked by `tests/test_repo_layout.py` (every path in them must resolve), so keep the feed paths correct.
- Do not copy real accounts, names or e-mail addresses here.
- For a real app, put the config under `onboarding/connectors/<type>/apps/` and the feed under `.../data/`; see
  [`onboarding/connectors/README.md`](../../onboarding/connectors/README.md).
