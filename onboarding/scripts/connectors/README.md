# `onboarding/scripts/connectors/`: one code file per connector

Each file implements one `connectorType`. The package is a registry: on start-up every module whose name does **not**
start with `_` is imported, and its class registers itself with `@register`. Nothing else in the code needs to change
when a connector is added, and a duplicate `type` raises an error at start-up.

The matching configuration (templates, app YAMLs, feeds) lives in
[`onboarding/connectors/<type>/`](../../connectors/README.md).

## Files
| File | `type` | What it does | Config folder |
|---|---|---|---|
| [`__init__.py`](__init__.py) | | The registry: `Connector` base class, `register`, `get`, `discover`, `UnknownConnector`. | |
| [`_template.py`](_template.py) | | Starter skeleton for a new connector. Not registered (leading `_`). | |
| [`_sourcetemplate.py`](_sourcetemplate.py) | | `SourceTemplate`, the shared base for JDBC, API, Windows and Linux: template lookup, the common checks, secrets from the environment, source creation, schema and correlation, aggregation without a file. Not registered. | |
| [`delimited.py`](delimited.py) | `delimited` | Delimited File source from a CSV; schema from the header; upload and aggregate. | [`delimited/`](../../connectors/delimited/README.md) |
| [`sqlloader.py`](sqlloader.py) | `sqlloader` | Delimited File source from a predefined template (schema and correlation fixed); upload and aggregate. | [`sqlloader/`](../../connectors/sqlloader/README.md) |
| [`ad.py`](ad.py) | `ad` | No source creation. Governance group, approvers and access profile per AD group in an export. `creates_source = False`. | [`ad/`](../../connectors/ad/README.md) |
| [`jdbc.py`](jdbc.py) | `jdbc` | `SourceTemplate`, `jdbcTemplates`, cluster required, URL must start `jdbc:`. | [`jdbc/`](../../connectors/jdbc/README.md) |
| [`api.py`](api.py) | `api` | `SourceTemplate`, `apiTemplates`, cluster optional. | [`api/`](../../connectors/api/README.md) |
| [`windows.py`](windows.py) | `windows` | `SourceTemplate`, `windowsTemplates`, cluster required. | [`windows/`](../../connectors/windows/README.md) |
| [`linux.py`](linux.py) | `linux` | `SourceTemplate`, `linuxTemplates`, cluster required. | [`linux/`](../../connectors/linux/README.md) |

## The contract
A connector subclasses `Connector` and sets `type`.

| Member | Purpose | Rules |
|---|---|---|
| `validate(app, defaults)` | Return a list of problem strings; empty means the app is fine. | **No API calls.** Runs for the whole batch before anything is sent, so one bad app stops the run early. |
| `handle(ob, app, result)` | Onboard one app using the `Onboarder` steps `ob`; record outcomes in `result` (a dict). | Set `result["status"] = "partial"` for a soft problem; raise `ISCError` for a hard one. `Onboarder.run` catches it, marks the app `failed`, and carries on with the batch. |
| `feed(app)` | For file-fed connectors: `(configured feed path, expected file name)`, else `None`. | Used by `batch.layout_errors` to enforce `.../data/{env}/<name>`. |
| `creates_source` | `True` by default; `False` when the source already exists (AD). | |

Steps available on `ob` (see [`onboarder.py`](../onboarder.py)): `ensure_source`, `apply_schema`, `apply_correlation`,
`aggregate`, `make_requestable`, `finish_non_ad`, `ensure_access_profile`, `ensure_source_app`,
`ensure_governance_group`, `find_one`, `identity_ref`, `approver_identity_id`.

## Add a connector
1. Copy [`_template.py`](_template.py) to `<type>.py` and set `type`.
   Or, for a source that ISC pulls itself, subclass `SourceTemplate` in about five lines (see [`jdbc.py`](jdbc.py)):
   set `type`, `templates_key`, `needs_cluster` and optionally `url_prefix`.
2. Implement `validate` and `handle` (and `feed` for a file-fed connector).
3. Create `onboarding/connectors/<type>/` with `<type>-template.yaml`, `README.md`, `apps/wave-1`, `apps/wave-2` and `data/`
   (copy the shape of an existing connector; `tests/test_repo_layout.py` and `tests/test_template_sources.py` check it).
4. Add tests: negative validation cases and a handler run against `FakeISC` (see `tests/test_jdbc.py`).
5. Update the connector table in [`onboarding/connectors/README.md`](../../connectors/README.md) and the main
   [`README.md`](../../../README.md), and add the new type to the known-types assertions in
   `tests/test_connector_registry.py`.
6. Run `python -m pytest -q tests` and `python onboarding/scripts/onboard.py --offline --apps "onboarding/connectors/*/apps/**/*.yaml*"`.

## Rules
- Keep a connector's rules in its own file; shared checks belong in [`validation.py`](../validation.py).
- A password, token or key never appears in YAML or in code. Use `secretEnv` for source credentials.
- Do not call the API from `validate`.
