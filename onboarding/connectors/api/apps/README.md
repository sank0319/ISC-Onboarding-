# `apps/`: application definitions for the `api` connector

One YAML per application (or one file with an `apps:` list). Each entry tells the pipeline what to create in ISC. The
connector's behaviour and every key are described in [`../README.md`](../README.md).

## What is in here now
`wave-1/sample-api.yaml.example` (inactive sample with a made-up host).

## Rules
- **Location.** Files live in `apps/` or in a wave folder `apps/wave-N/`. `wave-2/` is for everything after the pilot (empty for now). `--wave N` runs only that wave.
  A config placed anywhere else under `onboarding/connectors/` is rejected by `layout_errors` before any API call.
- **Type.** Every entry has `connectorType: api`; a config in this folder with another type is rejected.
- **Active or inactive.** The default glob reads `*.yaml` only. A file ending `.yaml.example` is ignored, which is how
  samples stay inactive. To activate one, copy it, drop `.example`, and edit the values.
- **Names.** `appName` is required and unique across the whole batch (upper/lower case and extra spaces are ignored when
  comparing). `owner` is an identity alias or e-mail that must exist in ISC.
- **No secrets.** Never put a password, token or key in an app YAML; see the connector README for `secretEnv`.
- **Environment.** Feed paths use `{env}` instead of `sandbox` or `production`; the run's `ISC_TARGET` fills it in.
- **Empty folders.** `.gitkeep` files exist only so git keeps an empty folder; leave them.

## Check a file without a tenant
```
python onboarding/scripts/onboard.py --validate-only --apps "onboarding/connectors/api/apps/**/*.yaml"
python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/api/apps/**/*.yaml*"
```
The first checks the config; the second also rehearses the whole run against an in-memory ISC.
