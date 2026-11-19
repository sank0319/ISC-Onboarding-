# `onboarding/config/`: global settings

Holds one file, [`defaults.yaml`](defaults.yaml): the settings that apply to **every** app of **every** connector.
Connector-specific settings do not live here; they are in each connector's own
`onboarding/connectors/<type>/<type>-template.yaml`.

## Keys in `defaults.yaml`
| Key | Default | Meaning |
|---|---|---|
| `deleteThreshold` | `10` | Delete threshold written on every source the pipeline creates. |
| `requestCommentRequired` | `true` | Whether access requests on created access profiles require a comment. |
| `updateExistingAccessProfiles` | `false` | `true` re-applies entitlements and approvals on access profiles that already exist. |
| `aggregationTimeoutMinutes` | `30` | How long to wait for an aggregation (or for uploaded accounts to appear) before the app fails. |
| `clusterId` | `""` | Global virtual appliance cluster ID, used by connectors that need one when their template does not set its own. Not needed for Delimited File or SQL Loader. |

Optional key read by the code: `postAggregationPauseSeconds` (default 20), the pause after a utility upload so
entitlements can settle.

## How the settings are assembled
`common.load_defaults` reads every `onboarding/connectors/*/*-template.yaml` first (in name order) and then
`defaults.yaml` on top, merging mappings recursively. **A value in `defaults.yaml` wins** over a template, so an older
all-in-one `defaults.yaml` keeps working. The merged result is what the connectors see as `defaults`.
An app's own YAML is layered over that at run time.

## Rules
- Keep only genuinely global settings here. A setting for one connector belongs in that connector's template.
- Never put a credential here. Credentials come from Vault (see the main [README](../../README.md#credentials-hashicorp-vault));
  source passwords come from environment variables named under `secretEnv`.
- Placeholders that start with `REPLACE_` are rejected by validation on purpose, so a half-configured connector cannot run.
  They live in the connector templates, not here.
- `--defaults FILE` on `onboard.py` points at a different file; the connector templates are found next to it
  (`<file's folder>/../connectors/`).

## Where to look
[`common.py`](../scripts/common.py) (`load_defaults`, `deep_merge`), the connector templates in
[`onboarding/connectors/`](../connectors/README.md), tests in
[`tests/test_offline.py`](../../tests/test_offline.py) (`test_template_files_are_merged_and_defaults_yaml_wins`).
