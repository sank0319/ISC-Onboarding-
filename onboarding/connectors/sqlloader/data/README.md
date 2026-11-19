# `data/`: feed files for the `sqlloader` connector

The files the pipeline reads for this connector. Behaviour and keys: [`../README.md`](../README.md).

## Layout
```
data/
  sandbox/      test data; committed
  production/   real data; supplied at run time, never committed
```

## What is in here now
`sandbox/Sample-SqlLoader-App.csv` (5-row sample feed).

## Rules
- **File name:** `<source name>.csv`. Validation rejects a feed path that does not
  end `.../connectors/sqlloader/data/{env}/<name>`.
- **Columns:** the template's schema columns by default: `USER_ID, USER_NAME, EMAIL, STATUS, ROLE`.
- **Format:** UTF-8 CSV with a header row; a leading byte-order mark is accepted. The header must have no blank or
  duplicate column names and the file must have at least one data row.
- **Environment:** the app YAML writes `{env}` in the path; `ISC_TARGET=production` selects `production/`, anything else
  selects `sandbox/`.
- **Personal data:** `data/production/*.csv` is git-ignored (see `.gitignore`). Supply production files at run time from
  approved secure storage and never commit them. Sandbox files must contain made-up data only.
- **Empty folders** keep a `.gitkeep` so git stores them.
