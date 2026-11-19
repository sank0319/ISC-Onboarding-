# `data/production/`: real feeds for the `sqlloader` connector (not committed)

Used when `ISC_TARGET=production`. Real account files contain personal data, so they are supplied **at run time** from
approved secure storage and are **never committed**. The `*.csv` files here are ignored by the root `.gitignore`.

## Rules
- Use the same file name as the sandbox feed: `<source name>.csv` (accounts) or `<app name>-group.csv` (AD groups).
- Do not commit, copy into a commit, or paste the contents of a file from here into an issue or a pull request.
- The folder must exist in git (it holds `.gitkeep`) so that the layout checks and the run can find it.
- A production run fails validation, before any API call, if the file is missing.

## Where to look
The connector: [`../../README.md`](../../README.md). Folder rules: [`../README.md`](../README.md).
