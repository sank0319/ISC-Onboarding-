# `data/`: feed files for the `delimited` connector

The files the pipeline reads for this connector. Behaviour and keys: [`../README.md`](../README.md).

## Layout
```
data/
  sandbox/      test data; committed
  production/   real data; supplied at run time, never committed
```

## What is in here now
`sandbox/Test-Delimited-App.csv` (pilot feed, columns `EmployeeID, UserName, Role`).

## Rules
- **File name:** `<source name>.csv`. Validation rejects a feed path that does not
  end `.../connectors/delimited/data/{env}/<name>`.
- **Columns:** one account per row; must contain the schema, entitlement and correlation columns named in the app YAML.
- **Format:** UTF-8 CSV with a header row; a leading byte-order mark is accepted. The header must have no blank or
  duplicate column names and the file must have at least one data row.
- **Environment:** the app YAML writes `{env}` in the path; `ISC_TARGET=production` selects `production/`, anything else
  selects `sandbox/`.
- **Personal data:** `data/production/*.csv` is git-ignored (see `.gitignore`). Supply production files at run time from
  approved secure storage and never commit them. Sandbox files must contain made-up data only.
- **Empty folders** keep a `.gitkeep` so git stores them.
