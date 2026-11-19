# `docs/examples/feeds/`: example feeds

Made-up account and group files used by [`../batch-01.yaml`](../batch-01.yaml) so the example config has real inputs.

| File | Format |
|---|---|
| [`Claims-Portal.csv`](Claims-Portal.csv) | SQL Loader style: `USER_ID,USER_NAME,EMAIL,STATUS,ROLE` |
| [`Finance-Portal.csv`](Finance-Portal.csv) | Delimited File account feed: `EmployeeID,UserName,Email,Status,Role` |
| [`Treasury-group.csv`](Treasury-group.csv) | AD group export: `cn,distinguishedName,description,extensionAttribute10` |

## Rules
- Made-up data only: no real names, accounts or e-mail addresses.
- These files are outside `onboarding/connectors/`, so the layout rules do not apply to them; they are used only by the examples.
- The example config and this folder are checked by `tests/test_repo_layout.py`; keep their paths in step.
- The real feed for an application goes in `onboarding/connectors/<type>/data/{env}/`, not here.
