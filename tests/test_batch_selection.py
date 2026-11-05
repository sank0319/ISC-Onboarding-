"""Waves, apps-CSV selection, environment-separated feeds and connector-folder layout (offline)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import batch
import onboard

CONN = "onboarding/connectors"


@pytest.fixture
def repo(tmp_path, delim_app, defaults, csv_file):
    """Builds a mini repo: repo.app('Name', wave=1) writes <tmp>/onboarding/connectors/delimited/apps/wave-1/Name.yaml
    plus sandbox and production feeds named after the source."""
    (tmp_path / "defaults.yaml").write_text(yaml.safe_dump(defaults))
    root = tmp_path / CONN / "delimited"

    def app(name, wave=1, kind="delimited", feeds=("sandbox", "production"), feed_name=None):
        for env in feeds:
            (root / "data" / env).mkdir(parents=True, exist_ok=True)
            (root / "data" / env / f"{name}.csv").write_text(Path(csv_file()).read_text())
        cfg = delim_app(appName=name, application=name, connectorType=kind,
                        delimited={"accountFile": f"{root}/data/{{env}}/{feed_name or name}.csv"},
                        accessProfiles=[{"name": f"{name} AP", "entitlementValues": ["Approver"]}])
        folder = root / "apps" / (f"wave-{wave}" if wave else "")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.yaml").write_text(yaml.safe_dump(cfg))

    return SimpleNamespace(app=app, root=root, tmp=tmp_path, glob=f"{tmp_path}/{CONN}/**/*.yaml",
                           defaults=str(tmp_path / "defaults.yaml"))


@pytest.fixture
def run_cli(monkeypatch, repo, isc, capsys):
    """run_cli(*args, target=None) -> (exit code, output, names of apps onboarded, constructed-client count)."""
    constructed = []
    monkeypatch.setattr(onboard, "ISCClient", lambda *a, dry_run=False, **k: (constructed.append(1), isc)[1])
    for k in ("ISC_TENANT", "ISC_CLIENT_ID", "ISC_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")

    def run(*args, target=None):
        monkeypatch.delenv("ISC_TARGET", raising=False)
        if target:
            monkeypatch.setenv("ISC_TARGET", target)
        report = repo.tmp / "report.json"
        monkeypatch.setattr("sys.argv", ["onboard.py", "--apps", repo.glob, "--defaults", repo.defaults,
                                         "--report", str(report), *args])
        with pytest.raises(SystemExit) as e:
            onboard.main()
            raise SystemExit(0)
        done = [r["app"] for r in json.loads(report.read_text())] if report.exists() else []
        return e.value.code, capsys.readouterr().out, done, len(constructed)
    return run


# ---------------------------------------------------------------- waves
@pytest.mark.parametrize("path,wave", [
    ("onboarding/connectors/delimited/apps/wave-1/a.yaml", 1),
    ("onboarding/connectors/sqlloader/apps/wave-12/a.yaml", 12),
    ("onboarding/connectors/ad/apps/a.yaml", None),
    ("onboarding/connectors/delimited/apps/wave-2/nested/a.yaml", 2),
    ("onboarding/connectors/delimited/apps/wave-x/a.yaml", None),
    ("onboarding/connectors/delimited/apps/wave-1.yaml", None),
])
def test_wave_comes_from_the_wave_folder(path, wave):
    assert batch.wave_of(path) == wave


def test_default_runs_all_waves_and_wave_option_runs_one(repo, run_cli):
    repo.app("Pilot", wave=1)
    repo.app("Rest-A", wave=2)
    repo.app("Rest-B", wave=2)
    assert run_cli()[2] == ["Pilot", "Rest-A", "Rest-B"]
    assert run_cli("--wave", "1")[2] == ["Pilot"]
    assert run_cli("--wave", "2")[2] == ["Rest-A", "Rest-B"]


def test_wave_with_no_configs_exits_before_any_api_call(repo, run_cli):
    repo.app("Pilot", wave=1)
    code, out, done, clients = run_cli("--wave", "2")
    assert code not in (0, None) and not clients and not done
    assert "in wave 2" in str(code)


def test_wave_option_skips_connectors_without_waves(repo, run_cli):
    repo.app("Pilot", wave=1)
    repo.app("Flat", wave=None)
    assert run_cli("--wave", "1")[2] == ["Pilot"]
    assert run_cli()[2] == ["Flat", "Pilot"]


@pytest.mark.parametrize("value", ["abc", "1.5", ""])
def test_wave_must_be_an_integer(repo, run_cli, value):
    repo.app("Pilot")
    code, _, _, clients = run_cli("--wave", value)
    assert code == 2 and not clients


# ---------------------------------------------------------------- apps CSV
@pytest.fixture
def csv_text(tmp_path):
    def write(text):
        p = tmp_path / "apps.csv"
        p.write_text(text)
        return str(p)
    return write


def entries(*names, kind="delimited"):
    return [(f"f/{n}.yaml", {"appName": n, "connectorType": kind}) for n in names]


def test_csv_selects_and_orders_apps(csv_text):
    got, errs = batch.select_apps(entries("A", "B", "C"), csv_text("applicationName,applicationType\nC,delimited\na,delimited\n"))
    assert errs == [] and [e[1]["appName"] for e in got] == ["C", "A"]


def test_csv_extra_columns_and_bom_are_fine(csv_text, tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes("﻿applicationName,note,applicationType\nA,x,delimited\n".encode())
    assert batch.select_apps(entries("A"), str(p))[1] == []


@pytest.mark.parametrize("text,msg", [
    ("", "no header row"),
    ("applicationName\nA\n", "missing ['applicationType']"),
    ("applicationType,name\ndelimited,A\n", "missing ['applicationName']"),
    ("applicationName,applicationType\n", "has no rows"),
    ("applicationName,applicationType\n,delimited\n", "line 2: applicationName and applicationType are both required"),
    ("applicationName,applicationType\nA,\n", "line 2: applicationName and applicationType are both required"),
    ("applicationName,applicationType\nA,oracle\n", "unknown applicationType 'oracle' (known: ['ad', 'delimited', 'sqlloader'])"),
    ("applicationName,applicationType\nNope,delimited\n", "line 2 (Nope): no app with this appName"),
    ("applicationName,applicationType\nA,delimited\nB,delimited\n a ,delimited\n", "line 4 (a): duplicate of line 2"),
    ("applicationName,applicationType\nA,ad\n", "applicationType 'ad' does not match connectorType 'delimited'"),
], ids=["empty", "no-type-col", "no-name-col", "no-rows", "blank-name", "blank-type", "unknown-type",
        "unknown-name", "duplicate", "type-mismatch"])
def test_csv_errors_are_clear(csv_text, text, msg):
    got, errs = batch.select_apps(entries("A", "B"), csv_text(text))
    assert any(msg in e for e in errs), errs
    assert "apps CSV" in errs[0]


def test_csv_missing_file(tmp_path):
    assert "apps CSV not found" in batch.select_apps(entries("A"), str(tmp_path / "nope.csv"))[1][0]


def test_csv_reports_every_bad_line_not_just_the_first(csv_text):
    _, errs = batch.select_apps(entries("A"), csv_text("applicationName,applicationType\nX,delimited\nY,oracle\nA,delimited\n"))
    assert len(errs) == 2


def test_csv_name_shared_by_two_configs_is_ambiguous(csv_text):
    _, errs = batch.select_apps(entries("A") + [("g/A.yaml", {"appName": "a", "connectorType": "delimited"})],
                                csv_text("applicationName,applicationType\nA,delimited\n"))
    assert "2 configs share this appName" in errs[0]


def test_cli_csv_creates_only_selected_apps_in_csv_order(repo, run_cli, csv_text):
    for n in ("A", "B", "C"):
        repo.app(n)
    code, _, done, _ = run_cli("--apps-csv", csv_text("applicationName,applicationType\nC,delimited\nA,delimited\n"))
    assert code == 0 and done == ["C", "A"]


def test_cli_unselected_broken_app_does_not_block_the_run(repo, run_cli, csv_text):
    repo.app("Good")
    repo.app("Broken", feed_name="Broken", feeds=())  # feed file missing
    assert run_cli("--apps-csv", csv_text("applicationName,applicationType\nGood,delimited\n"))[0] == 0
    assert run_cli()[0] == 1


def test_cli_bad_csv_exits_before_any_api_call(repo, run_cli, csv_text):
    repo.app("A")
    code, out, done, clients = run_cli("--apps-csv", csv_text("applicationName,applicationType\nZ,delimited\n"))
    assert code == 1 and not clients and not done and "Selection failed" in out and "Z" in out


def test_cli_csv_combines_with_wave(repo, run_cli, csv_text):
    repo.app("Pilot", wave=1)
    repo.app("Rest", wave=2)
    rest = csv_text("applicationName,applicationType\nRest,delimited\n")
    assert run_cli("--apps-csv", rest, "--wave", "2")[2] == ["Rest"]
    only_pilot = csv_text("applicationName,applicationType\nPilot,delimited\n")
    code, out, _, _ = run_cli("--apps-csv", only_pilot, "--wave", "2")
    assert code == 1 and "Selection failed" in out and "no app with this appName" in out


# ---------------------------------------------------------------- environments and feed naming
def test_with_env_replaces_placeholder_everywhere():
    cfg = {"a": "x/{env}/y", "b": [{"c": "{env}"}], "d": 3, "e": "{app} - {name}"}
    assert batch.with_env(cfg, "production") == {"a": "x/production/y", "b": [{"c": "production"}], "d": 3, "e": "{app} - {name}"}


@pytest.mark.parametrize("target,env", [("production", "production"), ("sandbox", "sandbox"),
                                        ("sandbox-readonly", "sandbox"), (None, "sandbox"), ("", "sandbox")])
def test_only_the_production_target_reads_production_feeds(target, env):
    assert batch.target_env(target) == env


def test_sandbox_run_reads_sandbox_feed_and_production_run_reads_production_feed(repo, run_cli, isc):
    repo.app("App")
    (repo.root / "data" / "production" / "App.csv").write_text("EmployeeID,UserName,Email,Status,Role\n9,prod,p@x.com,Active,Approver\n")
    assert run_cli(target="sandbox")[0] == 0
    sandbox_rows = isc.called("POST", "load-accounts")
    assert run_cli(target="production")[0] == 0
    assert len(isc.called("POST", "load-accounts")) == len(sandbox_rows) + 1


def test_production_run_without_production_feed_fails_validation(repo, run_cli):
    repo.app("App", feeds=("sandbox",))
    code, out, _, clients = run_cli(target="production")
    assert code == 1 and not clients and "data/production/App.csv" in out and "not found" in out
    assert run_cli(target="sandbox")[0] == 0


def test_validate_only_checks_the_sandbox_feed_by_default(repo, run_cli):
    repo.app("App", feeds=("production",))
    code, out, _, _ = run_cli("--validate-only")
    assert code == 1 and "data/sandbox/App.csv" in out


def layout(tmp_path, folder, app):
    return batch.layout_errors([(str(tmp_path / CONN / folder / "apps" / "wave-1" / "a.yaml"), app)])


def app_with(feed, **over):
    return {"appName": "My App", "connectorType": "delimited", "delimited": {"accountFile": feed}, **over}


GOOD = f"{CONN}/delimited/data/{{env}}/My App.csv"


def test_layout_accepts_convention(tmp_path):
    assert layout(tmp_path, "delimited", app_with(GOOD)) == []
    assert layout(tmp_path, "delimited", app_with(GOOD.replace("My App", "Other"), sourceName="Other")) == []


@pytest.mark.parametrize("feed", [
    f"{CONN}/delimited/data/sandbox/My App.csv",          # environment hard-coded
    f"{CONN}/delimited/data/{{env}}/my-app.csv",           # not named after the source
    f"{CONN}/delimited/data/{{env}}/My App-group.csv",     # group name on an account feed
    f"{CONN}/delimited/{{env}}/My App.csv",                 # missing data/ level
    f"{CONN}/sqlloader/data/{{env}}/My App.csv",           # another connector's feed folder
    "somewhere/else/My App.csv",
])
def test_layout_rejects_feed_outside_convention(tmp_path, feed):
    errs = layout(tmp_path, "delimited", app_with(feed))
    assert len(errs) == 1 and "feed file must be" in errs[0] and "data/{env}/My App.csv" in errs[0]


def test_layout_rejects_app_in_another_connectors_folder(tmp_path):
    errs = layout(tmp_path, "sqlloader", app_with(GOOD))
    assert "connectorType 'delimited' must live in onboarding/connectors/delimited/apps/" in errs[0]


def test_layout_rejects_file_directly_in_connectors_dir(tmp_path):
    errs = batch.layout_errors([(str(tmp_path / CONN / "a.yaml"), app_with(GOOD))])
    assert "must live in" in errs[0]


def test_ad_group_files_are_named_with_group_suffix(tmp_path):
    ad = {"appName": "Treasury", "connectorType": "ad", "ad": {"entitlementExport": f"{CONN}/ad/data/{{env}}/Treasury-group.csv"}}
    assert layout(tmp_path, "ad", ad) == []
    ad["ad"]["entitlementExport"] = f"{CONN}/ad/data/{{env}}/Treasury.csv"
    assert "Treasury-group.csv" in layout(tmp_path, "ad", ad)[0]


def test_layout_ignores_configs_outside_the_connectors_folder(tmp_path):
    assert batch.layout_errors([(str(tmp_path / "x.yaml"), app_with("anything.csv"))]) == []


def test_layout_survives_malformed_entries(tmp_path):
    for bad in ({"connectorType": "delimited", "delimited": "x"}, {"connectorType": ["ad"]}, {}, "text"):
        layout(tmp_path, "delimited", bad)


def test_cli_rejects_misplaced_feed(repo, run_cli):
    repo.app("App", feed_name="elsewhere")
    repo.app("Other")
    code, out, _, clients = run_cli()
    assert code == 1 and not clients and "feed file must be" in out


@pytest.mark.parametrize("sub", [("a.yaml",), ("data", "a.yaml"), ("wave-1", "a.yaml"), ("feeds", "a.yaml")])
def test_layout_requires_configs_under_apps(tmp_path, sub):
    """Each connector folder has apps/ (definitions) and data/ (feeds); a config anywhere else is rejected."""
    errs = batch.layout_errors([(str(tmp_path.joinpath(CONN, "delimited", *sub)), app_with(GOOD))])
    assert len(errs) == 1 and "must live in onboarding/connectors/delimited/apps/" in errs[0]


def test_layout_accepts_configs_without_wave_folder_under_apps(tmp_path):
    entries = [(str(tmp_path / CONN / "ad" / "apps" / "a.yaml"),
                {"appName": "Treasury", "connectorType": "ad",
                 "ad": {"entitlementExport": f"{CONN}/ad/data/{{env}}/Treasury-group.csv"}})]
    assert batch.layout_errors(entries) == []
