# `onboarding/`: everything the pipeline reads, runs and writes

This folder is the pipeline. Its four sub-folders separate **settings**, **per-connector configuration and data**,
**code**, and **output**, so each can be reviewed and changed on its own.

| Folder | What it holds | Who changes it | Details |
|---|---|---|---|
| [`config/`](config/README.md) | `defaults.yaml`: global settings (thresholds, timeouts, global cluster ID) | rarely; platform owners | [config/README.md](config/README.md) |
| [`connectors/`](connectors/README.md) | One folder per connector type: its template, its app definitions (`apps/`) and its feed files (`data/`) | routinely: this is where an application is added | [connectors/README.md](connectors/README.md) |
| [`scripts/`](scripts/README.md) | The Python code, with one file per connector in `scripts/connectors/` | developers; needs a code review and tests | [scripts/README.md](scripts/README.md) |
| [`logs/`](logs/README.md) | JSON-lines run logs, committed by the pipeline | nobody by hand | [logs/README.md](logs/README.md) |

## Why configuration and code are separate
Adding an application is a routine change (a YAML and a feed). Changing how the pipeline behaves is a code change.
Keeping them in different folders means a reviewer can tell from the path alone which kind of change a pull request is,
and a mistake in a config cannot alter the code.

## How a run moves through the folders
1. `scripts/onboard.py` reads `config/defaults.yaml` and every `connectors/<type>/<type>-template.yaml`.
2. It loads the app YAMLs from `connectors/<type>/apps/` and checks them, including that each feed file exists under
   `connectors/<type>/data/{env}/`.
3. It gets credentials, then runs each app through its connector in `scripts/connectors/`.
4. It writes a report (`onboarding-report.json`, git-ignored) and a run log under `logs/`.

## Rules
- A password, token or key never goes in any folder here. See [config/README.md](config/README.md) and the main
  [README](../README.md#credentials-hashicorp-vault).
- `connectors/*/data/production/*.csv` is git-ignored (personal data).
- `onboarding-report.json` (repo root) is written by each run. It is git-ignored, but the copy in git is still tracked (missing: a `git rm --cached` commit).
- Every directory has its own README that says what belongs in it; keep it up to date when you change the folder.

## Where to look
The repo overview and the full list of tools, APIs and connectors: [`README.md`](../README.md).
Layout decisions: [`docs/structure.md`](../docs/structure.md). Progress: [`docs/status.md`](../docs/status.md).
