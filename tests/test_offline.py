"""`onboard.py --offline`: the whole batch rehearsed against the simulator, and per-connector template loading."""
import json

import pytest
import yaml

import common
import onboard
import simulator
from conftest import ROOT

ALL_APPS = "onboarding/connectors/*/apps/**/*.yaml*"  # active configs and the inactive .yaml.example samples


@pytest.fixture
def from_repo_root(monkeypatch):
    monkeypatch.chdir(ROOT)


def run_offline(monkeypatch, tmp_path, *extra, apps=ALL_APPS):
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["onboard.py", "--offline", "--apps", apps, "--report", str(report), *extra])
    with pytest.raises(SystemExit) as e:
        onboard.main()
    return e.value.code, report


def test_every_checked_in_config_and_sample_rehearses_cleanly(from_repo_root, monkeypatch, tmp_path):
    code, report = run_offline(monkeypatch, tmp_path)
    results = json.loads(report.read_text())
    assert code == 0, results
    assert {r["type"] for r in results} >= {"delimited", "sqlloader", "jdbc", "api", "windows", "linux"}
    assert all(r["status"] == "ok" for r in results)


def test_offline_never_asks_for_credentials_or_the_utility(from_repo_root, monkeypatch, tmp_path):
    for k in ("ISC_TENANT", "ISC_CLIENT_ID", "ISC_CLIENT_SECRET", "VAULT_ADDR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ISC_UPLOAD_JAR", "/does/not/exist.jar")
    monkeypatch.setattr(onboard, "load_credentials", lambda *a, **k: pytest.fail("offline must not load credentials"))
    code, _ = run_offline(monkeypatch, tmp_path)
    assert code == 0


def test_offline_dry_run_creates_nothing(from_repo_root, monkeypatch, tmp_path):
    code, report = run_offline(monkeypatch, tmp_path, "--dry-run")
    assert code == 0 and json.loads(report.read_text())


def test_offline_reports_a_broken_config_like_a_real_run(from_repo_root, monkeypatch, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"appName": "Bad", "connectorType": "nope", "owner": "x"}))
    monkeypatch.setattr("sys.argv", ["onboard.py", "--offline", "--apps", str(bad)])
    with pytest.raises(SystemExit) as e:
        onboard.main()
    assert e.value.code == 1


def test_placeholder_connectors_are_simulated_not_changed_on_disk(defaults):
    defaults["jdbcTemplates"]["default"].update(connector="REPLACE_WITH_JDBC_SCRIPT_NAME", clusterId="")
    out, notes = simulator.fill_placeholders(defaults)
    assert out["jdbcTemplates"]["default"]["connector"].startswith("offline-")
    assert out["jdbcTemplates"]["default"]["clusterId"] == "offline-cluster"
    assert any("jdbcTemplates.default" in n for n in notes)
    assert defaults["jdbcTemplates"]["default"]["connector"].startswith("REPLACE")  # input untouched


def test_configured_templates_are_left_alone(defaults):
    out, notes = simulator.fill_placeholders(defaults)
    assert out["jdbcTemplates"]["default"]["connector"] == "jdbc-script"  # configured: untouched
    assert not any("jdbcTemplates" in n for n in notes)


# ---- per-connector template.yaml files
def test_template_files_are_merged_and_defaults_yaml_wins(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "connectors" / "x").mkdir(parents=True)
    (tmp_path / "connectors" / "x" / "template.yaml").write_text("xTemplates:\n  default: {a: 1, b: 2}\nkeep: from-template\n")
    (tmp_path / "config" / "defaults.yaml").write_text("xTemplates:\n  default: {b: 3}\ntop: 1\n")
    got = common.load_defaults(tmp_path / "config" / "defaults.yaml")
    assert got == {"xTemplates": {"default": {"a": 1, "b": 3}}, "keep": "from-template", "top": 1}


def test_defaults_without_a_connectors_folder_still_load(tmp_path):
    (tmp_path / "defaults.yaml").write_text("top: 1\n")
    assert common.load_defaults(tmp_path / "defaults.yaml") == {"top": 1}


def test_checked_in_defaults_carry_every_connector_template():
    d = common.load_defaults(ROOT / "onboarding" / "config" / "defaults.yaml")
    for key in ("sqlLoaderTemplates", "jdbcTemplates", "apiTemplates", "windowsTemplates", "linuxTemplates", "ad",
                "connectors", "delimitedConnectorAttributes"):
        assert key in d, key
    assert "sqlLoaderTemplates" not in common.load_yaml(ROOT / "onboarding" / "config" / "defaults.yaml")  # global file only
