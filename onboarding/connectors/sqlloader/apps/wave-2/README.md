# `apps/wave-2/`: wave 2 for the `sqlloader` connector

Holds the app definitions (one YAML per application, or one file with an `apps:` list) in **wave 2**: every application after the pilot. Created after wave 1 is proven. `--wave 2` runs only this folder.

## Rules
- Only `*.yaml` files are read. A `.yaml.example` file is an inactive sample; copy it and drop `.example` to activate.
- Every entry must have `connectorType: sqlloader`; the folder name and the type must match, or validation fails.
- `appName` must be unique across the whole batch, and feed paths must use `{env}`
  (`onboarding/connectors/sqlloader/data/{env}/...`).
- No passwords, tokens or keys in these files; use `secretEnv` where the connector needs one.
- `.gitkeep` keeps the folder in git when it is empty; leave it in place.

## Check a file without a tenant
```
python onboarding/scripts/onboard.py --wave 2 --validate-only
python onboarding/scripts/onboard.py --wave 2 --offline
```
The first only validates; the second rehearses the wave against the in-memory ISC simulator.

## Where to look
The connector: [`../../README.md`](../../README.md). The folder rules: [`../README.md`](../README.md). Connector code:
[`onboarding/scripts/connectors/sqlloader.py`](../../../../scripts/connectors/sqlloader.py) (for `api`, `jdbc`, `windows` and `linux`,
the shared base is [`_sourcetemplate.py`](../../../../scripts/connectors/_sourcetemplate.py)).
