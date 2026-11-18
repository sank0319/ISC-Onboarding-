"""Feeds uploaded with SailPoint's File Upload Utility JAR (ISC_UPLOAD_JAR) instead of the load-accounts call."""
import os
import subprocess
from types import SimpleNamespace

import pytest

import uploader
from conftest import SQL_CSV_TEXT, run_batch
from isc_client import ISCError
from run_log import register_secret

SID = "2c9180835d191a86015d27b0a2c31334"


@pytest.fixture
def jar(tmp_path, monkeypatch):
    p = tmp_path / "file-upload-utility.jar"
    p.write_bytes(b"jar")
    monkeypatch.setenv("ISC_UPLOAD_JAR", str(p))
    return str(p)


@pytest.fixture
def sql_app(csv_file):
    return {"appName": "Sql-App", "connectorType": "sqlloader", "owner": "asmith", "application": "Sql App",
            "sqlloader": {"accountFile": csv_file(SQL_CSV_TEXT, name="Sql-App.csv")},
            "accessProfiles": [{"name": "Sql App - Role", "entitlementValues": ["ROLE-A"]}]}


@pytest.fixture
def fake_ids(monkeypatch):
    """FakeISC ids look like 'src8'; real ISC source ids are 32 hex characters (checked by the id tests below)."""
    import re
    monkeypatch.setattr(uploader, "SOURCE_ID", re.compile(r"^\w+$"))


def fake_utility(isc, calls, returncode=0, out="1 file aggregated"):
    """Stands in for `java -jar`: records the call and does what the real tool does (loads the staged file)."""
    def run(cmd, env, capture_output, text, timeout):
        staged = cmd[-1]
        sid = os.path.basename(staged).split("-", 1)[0]
        calls.append(SimpleNamespace(cmd=cmd, env=env, staged=staged, name=os.path.basename(staged)))
        if returncode == 0:
            with open(staged, "rb") as fh:
                isc.request("POST", f"/v2024/sources/{sid}/load-accounts", files={"file": ("f.csv", fh, "text/csv")},
                            write=True)
        return subprocess.CompletedProcess(cmd, returncode, out, "")
    return run


def test_sqlloader_feed_goes_through_the_utility(isc, defaults, sql_app, jar, fake_ids, monkeypatch):
    calls = []
    monkeypatch.setattr(uploader, "_run", fake_utility(isc, calls))
    res = run_batch(isc, defaults, [sql_app])[0]
    assert res["entitlementsRequestable"] == 1
    assert len(calls) == 1
    c = calls[0]
    src_id = c.name.split("-", 1)[0]
    assert src_id in {s["id"] for s in isc.sources.values()}  # staged file is named after the real source id
    assert c.name == f"{src_id}-Sql-App.csv"          # utility finds the source from the file name
    assert c.cmd[:3] == ["java", "-jar", jar] and "-u" in c.cmd
    assert c.env["SAIL_CLIENT_ID"] == isc.client_id and c.env["SAIL_CLIENT_SECRET"] == isc.client_secret
    assert isc.client_secret not in " ".join(c.cmd)    # credentials never on the command line


def test_without_the_jar_variable_the_api_call_is_used(isc, defaults, sql_app, monkeypatch):
    monkeypatch.setattr(uploader, "_run", lambda *a, **k: pytest.fail("utility must not run"))
    run_batch(isc, defaults, [sql_app])
    ids = {s["id"] for s in isc.sources.values()}
    assert any(m == "POST" and p.endswith("/load-accounts") and p.split("/")[3] in ids for m, p in isc.writes)


def test_dry_run_never_runs_the_utility(isc, defaults, sql_app, jar, monkeypatch):
    isc.dry_run = True
    monkeypatch.setattr(uploader, "_run", lambda *a, **k: pytest.fail("utility must not run"))
    run_batch(isc, defaults, [sql_app])


def test_utility_failure_is_reported_with_redacted_output(isc, defaults, sql_app, jar, fake_ids, monkeypatch):
    out = f"error: bad credential {isc.client_secret}"
    monkeypatch.setattr(uploader, "_run", fake_utility(isc, [], returncode=1, out=out))
    register_secret(isc.client_secret)
    result = run_batch(isc, defaults, [sql_app])[0]
    text = str(result)
    assert "exit 1" in text and isc.client_secret not in text


@pytest.mark.parametrize("bad_id", ["", "123", "not-hex-" + "0" * 24])
def test_source_id_must_be_a_32_char_id(tmp_path, jar, bad_id):
    f = tmp_path / "a.csv"
    f.write_text("a\n1\n")
    with pytest.raises(ISCError, match="32-character"):
        uploader.upload_with_utility(jar, "https://x", "id", "secret", bad_id, str(f))


def test_missing_jar_or_file_is_an_error(tmp_path, jar):
    f = tmp_path / "a.csv"
    f.write_text("a\n1\n")
    with pytest.raises(ISCError, match="JAR not found"):
        uploader.upload_with_utility(str(tmp_path / "nope.jar"), "https://x", "id", "s", SID, str(f))
    with pytest.raises(ISCError, match="feed file not found"):
        uploader.upload_with_utility(jar, "https://x", "id", "s", SID, str(tmp_path / "missing.csv"))


def test_timeout_and_missing_java_are_reported(tmp_path, jar):
    f = tmp_path / "a.csv"
    f.write_text("a\n1\n")

    def boom(*a, **k):
        raise FileNotFoundError("java")
    with pytest.raises(ISCError, match="did not run"):
        uploader.upload_with_utility(jar, "https://x", "id", "s", SID, str(f), run=boom)
