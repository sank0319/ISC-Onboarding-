# `apps/`: application definitions for the `ad` connector

One YAML per application (or one file with an `apps:` list). Each entry tells the pipeline what to create in ISC for an
AD group export. The connector's behaviour and every key are described in [`../README.md`](../README.md).

## What is in here now
Nothing yet. AD apps are added once a real AD source and group export exist (missing: both, on the tenant).

## Rules
- **Location.** Files live directly in `apps/`. AD has no waves, so `--wave N` skips AD apps, and there is no `wave-1/` or `wave-2/` folder here.
  A config placed anywhere else under `onboarding/connectors/` is rejected by `layout_errors` before any API call.
- **Type.** Every entry has `connectorType: ad`; a config in this folder with another type is rejected.
- **Active or inactive.** The default glob reads `*.yaml` only. A file ending `.yaml.example` is ignored, which is how
  samples stay inactive. To activate one, copy it, drop `.example`, and edit the values.
- **Names.** `appName` is required and unique across the whole batch (upper/lower case and extra spaces are ignored when
  comparing). `owner` is an identity alias or e-mail that must exist in ISC.
- **No secrets.** Never put a password, token or key in an app YAML.
- **Environment.** The `ad.entitlementExport` path uses `{env}` (`data/{env}/<app name>-group.csv`); `ISC_TARGET` fills it in.

## Check a file without a tenant
```
python onboarding/scripts/onboard.py --validate-only --apps "onboarding/connectors/ad/apps/**/*.yaml"
python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/ad/apps/**/*.yaml*"
```
The first checks the config; the second also rehearses the whole run against an in-memory ISC.
