"""The checked-in connector folders: every config follows the layout and every path resolves."""
import glob
from pathlib import Path

import pytest

import batch
from common import load_defaults
from conftest import ROOT

CONFIGS = sorted(glob.glob(str(ROOT / "onboarding" / "connectors" / "*" / "apps" / "**" / "*.yaml"), recursive=True))
EXAMPLES = sorted(glob.glob(str(ROOT / "docs" / "examples" / "*.yaml")))
FILE_KEYS = {"delimited": ("accountFile",), "sqlloader": ("accountFile",), "ad": ("entitlementExport",)}


def rel(p):
    return str(Path(p).relative_to(ROOT))


@pytest.fixture(autouse=True)
def from_repo_root(monkeypatch):
    monkeypatch.chdir(ROOT)  # configs use repo-root-relative paths


def test_there_are_configs_to_check():
    assert CONFIGS and EXAMPLES


@pytest.mark.parametrize("path", CONFIGS, ids=rel)
def test_connector_configs_follow_layout_and_their_sandbox_feed_exists(path):
    entries = [(rel(path), a) for a in batch.file_apps(path)]
    assert batch.layout_errors(entries) == []
    for _, app in entries:
        for key in FILE_KEYS.get(app["connectorType"], ()):
            feed = app[app["connectorType"]][key]
            assert "{env}" in feed
            assert Path(batch.with_env(feed, "sandbox")).is_file(), feed
            assert (ROOT / Path(feed).parent.parent / "production").is_dir()  # production folder exists, filled at run time


@pytest.mark.parametrize("path", CONFIGS + EXAMPLES, ids=rel)
def test_every_config_file_path_resolves(path):
    for app in batch.file_apps(path):
        for key in FILE_KEYS.get(app["connectorType"], ()):
            assert Path(batch.with_env(app[app["connectorType"]][key], "sandbox")).is_file()


def test_connectors_with_waves_keep_configs_in_wave_folders():
    for path in CONFIGS:
        if Path(path).relative_to(ROOT).parts[2] in ("delimited", "sqlloader"):
            assert batch.wave_of(path) is not None, rel(path)


def test_example_batch_is_outside_the_default_glob():
    default = glob.glob(str(ROOT / "onboarding" / "connectors" / "*" / "apps" / "**" / "*.yaml"), recursive=True)
    assert not set(EXAMPLES) & set(default)


def test_default_glob_validates_with_the_checked_in_defaults():
    entries = batch.load_apps("onboarding/connectors/*/apps/**/*.yaml")
    defaults = load_defaults("onboarding/config/defaults.yaml")
    apps = [batch.with_env(a, "sandbox") for _, a in entries]
    assert batch.layout_errors(entries) == [] and batch.batch_errors(apps, defaults) == []


def test_example_apps_csv_selects_the_checked_in_app():
    entries = batch.load_apps("onboarding/connectors/*/apps/**/*.yaml")
    picked, errs = batch.select_apps(entries, "docs/examples/apps.csv")
    assert errs == [] and [a["appName"] for _, a in picked] == ["Test-Delimited-App"]


SQL_EXAMPLE = ROOT / "onboarding" / "connectors" / "sqlloader" / "apps" / "wave-1" / "sample-sqlloader.yaml.example"


def test_sqlloader_skeleton_has_the_two_folders_and_waves():
    base = ROOT / "onboarding" / "connectors" / "sqlloader"
    for sub in ("apps/wave-1", "apps/wave-2", "data/sandbox", "data/production"):
        assert (base / sub).is_dir(), sub
    assert (base / "README.md").is_file()


def test_sqlloader_example_is_inactive_until_renamed():
    assert SQL_EXAMPLE.is_file()
    assert str(SQL_EXAMPLE) not in glob.glob(str(ROOT / "onboarding" / "connectors" / "*" / "apps" / "**" / "*.yaml"), recursive=True)


def test_sqlloader_example_is_valid_once_activated(defaults):
    """Guards the sample against rotting: renamed to .yaml and with the connector set, it passes every check."""
    path = SQL_EXAMPLE.with_suffix("")  # .../sample-sqlloader.yaml (the name it gets when activated)
    apps = batch.file_apps(str(SQL_EXAMPLE))
    entries = [(rel(path), a) for a in apps]
    assert batch.layout_errors(entries) == []
    assert batch.batch_errors(batch.with_env(apps, "sandbox"), defaults) == []  # {env} resolves at run time
    assert batch.wave_of(path) == 1


def test_sqlloader_example_is_rejected_while_the_connector_is_a_placeholder():
    cfg = load_defaults("onboarding/config/defaults.yaml")
    cfg["sqlLoaderTemplates"]["default"]["connector"] = "REPLACE_WITH_SQL_LOADER_SCRIPT_NAME"
    errs = batch.batch_errors(batch.with_env(batch.file_apps(str(SQL_EXAMPLE)), "sandbox"), cfg)
    assert any("connector script name not set" in e for e in errs)


def test_sqlloader_apps_use_the_delimited_file_connector():
    cfg = load_defaults("onboarding/config/defaults.yaml")
    assert cfg["sqlLoaderTemplates"]["default"]["connector"] == cfg["connectors"]["delimited"]
    assert not cfg["sqlLoaderTemplates"]["default"].get("clusterId")


JDBC_EXAMPLE = ROOT / "onboarding" / "connectors" / "jdbc" / "apps" / "wave-1" / "sample-jdbc.yaml.example"


def test_jdbc_example_is_inactive_and_valid_once_the_tenant_values_are_set(defaults):
    assert not list(JDBC_EXAMPLE.parent.glob("*.yaml"))  # inactive until renamed
    path = JDBC_EXAMPLE.with_suffix("")
    apps = batch.file_apps(str(JDBC_EXAMPLE))
    assert batch.layout_errors([(rel(path), a) for a in apps]) == []
    assert batch.batch_errors(apps, defaults) == []  # the defaults fixture stands in for the real script name + cluster
    assert apps[0]["jdbc"]["connectorAttributes"]["url"].startswith("jdbc:")
    assert "password" not in apps[0]["jdbc"]["connectorAttributes"]
