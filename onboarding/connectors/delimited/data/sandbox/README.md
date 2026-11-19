# `data/sandbox/`: test feeds for the `delimited` connector

Used when `ISC_TARGET` is not `production` (the default, and every dry run in CI). Files here are **committed**, so they
must hold made-up data only.

## Rules
- File name: `<source name>.csv` for accounts, or `<app name>-group.csv` for AD group exports. Validation rejects any other path.
- The app YAML points here as `onboarding/connectors/delimited/data/{env}/<name>`; `{env}` becomes `sandbox` here.
- UTF-8, header row, no blank or duplicate column names, at least one data row. A leading byte-order mark is accepted.
- Every schema, entitlement and correlation column named in the app must be in the header.
- Keep `.gitkeep` if the folder is empty.

## Where to look
The connector: [`../../README.md`](../../README.md). Folder rules: [`../README.md`](../README.md).
