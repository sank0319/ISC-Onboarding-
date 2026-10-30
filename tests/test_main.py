"""CLI behaviour: validation gate, summary report and exit codes (ISCClient replaced by the fake)."""
import json

import pytest
import yaml

import onboard

APP = {"appName": "Cli-App", "connectorType": "delimited", "owner": "asmith", "application": "Cli App",
       "delimited": {"schema": {"identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                                "entitlementColumns": ["Role"]},
                     "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]},
       "accessProfiles": [{"name": "Cli AP", "entitlementValues": ["Approver"]}]}


@pytest.fixture
def cli(tmp_path, monkeypatch, isc, defaults, csv_file):
    constructed = []
    monkeypatch.setattr(onboard, "ISCClient", lambda *a, dry_run=False, **k: (constructed.append(1), setattr(isc, "dry_run", dry_run), isc)[2])
    for k in ("ISC_TENANT", "ISC_CLIENT_ID", "ISC_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")
    (tmp_path / "defaults.yaml").write_text(yaml.safe_dump(defaults))
    report = tmp_path / "report.json"

    def run(*files, extra=(), **apps_by_file):
        for name, content in (files and dict(files) or apps_by_file).items():
            (tmp_path / f"{name}.yaml").write_text(content if isinstance(content, str) else yaml.safe_dump(content))
        argv = ["onboard.py", "--apps", str(tmp_path / "*.yaml").replace("*.yaml", "app_*.yaml"),
                "--defaults", str(tmp_path / "defaults.yaml"), "--report", str(report), *extra]
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as e:
            onboard.main()
            raise SystemExit(0)
        return e.value.code, constructed, report

    run.file = csv_file()
    return run


def good(cli, **over):
    app = json.loads(json.dumps(APP))
    app["delimited"]["accountFile"] = cli.file
    app.update(over)
    return {"apps": [app]}


def test_all_ok_exits_zero_and_writes_report(cli):
    code, _, report = cli(app_a=good(cli))
    assert code == 0 and json.loads(report.read_text())[0]["status"] == "ok"


def test_validation_error_exits_before_any_api_call(cli, capsys):
    code, constructed, report = cli(app_a=good(cli, owner=""))
    assert code == 1 and not constructed and not report.exists() and "owner" in capsys.readouterr().out


def test_duplicate_name_across_files_exits_before_any_api_call(cli):
    code, constructed, _ = cli(app_a=good(cli), app_b=good(cli, appName="cli-app", application="Other"))
    assert code == 1 and not constructed


def test_validate_only_makes_no_api_calls(cli):
    code, constructed, report = cli(app_a=good(cli), extra=["--validate-only"])
    assert code == 0 and not constructed and not report.exists()


def test_failed_app_gives_non_zero_exit_and_report(cli, isc):
    isc.fail("POST", r"/v3/sources$", 500)
    code, _, report = cli(app_a=good(cli))
    assert code == 1 and json.loads(report.read_text())[0]["status"] == "failed"


def test_partial_app_gives_non_zero_exit(cli):
    code, _, report = cli(app_a=good(cli, accessProfiles=[{"name": "AP", "entitlementValues": ["Nope"]}]))
    assert code == 1 and json.loads(report.read_text())[0]["status"] == "partial"


def test_files_are_processed_in_sorted_order(cli):
    code, _, report = cli(app_b=good(cli, appName="B-App", application="B"), app_a=good(cli, appName="A-App", application="A"))
    assert [r["app"] for r in json.loads(report.read_text())] == ["A-App", "B-App"]


def test_no_matching_files(cli):
    code, constructed, _ = cli()
    assert code not in (0, None) and not constructed


@pytest.mark.parametrize("content", ["apps:\n", "apps: []\n", "- just\n- a list\n", "apps: {a: 1}\n", "apps: [unclosed\n",
                                     "", "just a string\n"], ids=["null", "empty-list", "top-list", "apps-dict", "bad-yaml", "empty", "scalar"])
def test_broken_yaml_files_exit_with_message_not_traceback(cli, content):
    code, constructed, _ = cli(app_a=content)
    assert code not in (0, None) and not constructed
