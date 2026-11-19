# `apps/wave-1/`: wave 1 for the `jdbc` connector

Holds the app definitions (one YAML per application, or one file with an `apps:` list) in **wave 1**: the pilot: one application, created first to prove the connector end to end. `--wave 1` runs only this folder.

## Rules
- Only `*.yaml` files are read. A `.yaml.example` file is an inactive sample; copy it and drop `.example` to activate.
- Every entry must have `connectorType: jdbc`; the folder name and the type must match, or validation fails.
- `appName` must be unique across the whole batch, and feed paths must use `{env}`
  (`onboarding/connectors/jdbc/data/{env}/...`).
- No passwords, tokens or keys in these files; use `secretEnv` where the connector needs one.
- `.gitkeep` keeps the folder in git when it is empty; leave it in place.

## Check a file without a tenant
```
python onboarding/scripts/onboard.py --wave 1 --validate-only
python onboarding/scripts/onboard.py --wave 1 --offline
```
The first only validates; the second rehearses the wave against the in-memory ISC simulator.

## Where to look
The connector: [`../../README.md`](../../README.md). The folder rules: [`../README.md`](../README.md). Connector code:
[`onboarding/scripts/connectors/jdbc.py`](../../../../scripts/connectors/jdbc.py) (for `api`, `jdbc`, `windows` and `linux`,
the shared base is [`_sourcetemplate.py`](../../../../scripts/connectors/_sourcetemplate.py)).
