# `data/`: group exports for the `ad` connector

The files the pipeline reads for this connector: one AD group export per application. Behaviour and keys: [`../README.md`](../README.md).

## Layout
```
data/
  sandbox/      test exports; committed (made-up data only)
  production/   real exports; supplied at run time, never committed
```
Both folders hold a `.gitkeep` until the first export is added. An example export is
[`docs/examples/feeds/Treasury-group.csv`](../../../../docs/examples/feeds/Treasury-group.csv).

## Rules
- **File name:** `<app name>-group.csv`. Validation rejects a feed path that does not
  end `.../connectors/ad/data/{env}/<name>`.
- **Columns:** the value column (`distinguishedName`), the name column (`cn`), the approval attribute (`extensionAttribute10`,
  written `<<a@x.com; b@x.com>>`) and optionally `description`.
- **Format:** UTF-8 CSV with a header row; a leading byte-order mark is accepted. The header must have no blank or
  duplicate column names and the file must have at least one data row.
- **Environment:** the app YAML writes `{env}` in the path; `ISC_TARGET=production` selects `production/`, anything else
  selects `sandbox/`.
- **Personal data:** `data/production/*.csv` is git-ignored by the root `.gitignore`. Supply production files at run time from
  approved secure storage and never commit them. Sandbox files must contain made-up data only.
- **Empty folders** keep a `.gitkeep` so git stores them.
