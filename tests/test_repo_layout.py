"""The checked-in connector folders: every config follows the layout and every path resolves."""
import glob
from pathlib import Path

import pytest

import batch
from common import load_yaml
from conftest import ROOT

CONFIGS = sorted(glob.glob(str(ROOT / "onboarding" / "connectors" / "**" / "*.yaml"), recursive=True))
EXAMPLES = sorted(glob.glob(str(ROOT / "docs" / "examples" / "*.yaml")))
FILE_KEYS = {"delimited": ("accountFile",), "ad": ("entitlementExport",)}


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
    default = glob.glob(str(ROOT / "onboarding" / "connectors" / "**" / "*.yaml"), recursive=True)
    assert not set(EXAMPLES) & set(default)


def test_default_glob_validates_with_the_checked_in_defaults():
    entries = batch.load_apps("onboarding/connectors/**/*.yaml")
    defaults = load_yaml("onboarding/config/defaults.yaml")
    apps = [batch.with_env(a, "sandbox") for _, a in entries]
    assert batch.layout_errors(entries) == [] and batch.batch_errors(apps, defaults) == []


def test_example_apps_csv_selects_the_checked_in_app():
    entries = batch.load_apps("onboarding/connectors/**/*.yaml")
    picked, errs = batch.select_apps(entries, "docs/examples/apps.csv")
    assert errs == [] and [a["appName"] for _, a in picked] == ["Test-Delimited-App"]
