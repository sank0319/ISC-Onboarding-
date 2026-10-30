import json
import re
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "onboarding" / "scripts"))

import isc_client  # noqa: E402
import onboard  # noqa: E402
from isc_client import ISCClient, ISCError  # noqa: E402
from run_log import RunLog, redact  # noqa: E402

SECRET = "fake-S3CRET-value-123"


def read_events(runlog):
    return [json.loads(line) for line in runlog.path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def runlog_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.setenv("ISC_CLIENT_SECRET", SECRET)
    return lambda **kw: RunLog(tmp_path / "logs", **kw)


# ---------------------------------------------------------------- redact
@pytest.mark.parametrize("raw, leaked", [
    ("Authorization: Bearer abcdefgh12345678", "abcdefgh12345678"),
    ('{"access_token": "tok.en-value", "x": 1}', "tok.en-value"),
    ('"{\\"client_secret\\": \\"zz9top\\"}"', "zz9top"),
    ("password=hunter22&user=x", "hunter22"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "eyJhbGci"),
    ("blob " + "k9" * 25, "k9" * 25),
])
def test_redact_secrets(raw, leaked):
    out = redact(raw)
    assert leaked not in out and "[REDACTED]" in out


def test_redact_masks_email_and_keeps_normal_text():
    assert redact("approver jane.doe@corp.example not found") == "approver j***@corp.example not found"
    sha = "51b96d0" + "a" * 33
    assert redact(f"sha {sha}") == f"sha {sha}"
    text = "created source 'Test App' (2c91808a7d1e4b3f9a0b1c2d3e4f5a6b) via /v3/sources"
    assert redact(text) == text


def test_redact_is_idempotent():
    once = redact("Bearer abcdefgh12345678 a.b@c.io token=xyz123")
    assert redact(once) == once


# ---------------------------------------------------------------- RunLog
def test_event_schema_and_context(runlog_factory):
    rl = runlog_factory(target="sandbox", dry_run=True)
    rl.app = "App1"
    rl.event("source_created", message="made it", step="source", id="s1")
    rl.event("api_error", "ERROR", method="GET", path="/x", status=500, tracking_id="t-1", body="x" * 1000)
    rl.close()
    start, created, err = read_events(rl)
    assert start["event"] == "run_start" and start["run"] == "4242"
    assert (start["attempt"], start["actor"], start["target"]) == ("2", "octocat", "sandbox")
    assert created["app"] == "App1" and created["step"] == "source" and created["dry_run"] is True
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", created["ts"])
    assert err["level"] == "ERROR" and err["tracking_id"] == "t-1" and len(err["body"]) == 300
    assert rl.path.parent.parent.name == "logs" and rl.path.name.endswith("Z-4242.jsonl")


def test_failure_to_open_or_write_is_not_fatal(tmp_path, runlog_factory):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    broken = RunLog(blocker / "logs")
    broken.event("anything")
    broken.end([{"app": "a", "status": "ok"}])

    rl = runlog_factory()

    class Boom:
        def write(self, _):
            raise OSError("disk full")

        def close(self):
            pass

    rl._fh = Boom()
    rl.event("x")
    rl.event("y")  # log is switched off, still no exception
    rl.end([])


def test_failure_event_has_redacted_traceback(runlog_factory):
    rl = runlog_factory()
    try:
        raise ValueError(f"boom client_secret={SECRET} for a.b@corp.io")
    except ValueError as e:
        rl.failure("run_crashed", e)
    rl.close()
    text = rl.path.read_text()
    assert SECRET not in text and "a.b@corp.io" not in text
    ev = read_events(rl)[-1]
    assert ev["error"] == "ValueError" and "Traceback" in ev["traceback"]


# ---------------------------------------------------------------- ISCClient events
def resp(status, body="", **headers):
    r = requests.Response()
    r.status_code, r._content, r.encoding = status, body.encode(), "utf-8"
    r.headers.update(headers)
    return r


def test_client_emits_retry_error_and_dry_run_events(monkeypatch):
    events = []
    client = ISCClient("t", "id", "secret", on_event=lambda e, lvl, **f: events.append((e, lvl, f)))
    client._auth = lambda force=False: None
    monkeypatch.setattr(isc_client.time, "sleep", lambda s: None)
    replies = iter([resp(429, "slow down", **{"Retry-After": "1"}),
                    resp(404, json.dumps({"trackingId": "trk-9", "detail": "nope"}))])
    client.session.request = lambda *a, **k: next(replies)
    with pytest.raises(ISCError):
        client.request("GET", "/v3/sources/1")
    assert [e[0] for e in events] == ["api_retry", "api_error"]
    assert events[0][2]["status"] == 429 and events[0][2]["wait"] == 1
    assert events[1][2]["tracking_id"] == "trk-9" and events[1][1] == "ERROR"

    client.dry_run = True
    client.request("POST", "/v3/sources", json_body={"name": "x"}, write=True)
    assert events[-1][0] == "dry_run_write" and "json_body" not in events[-1][2]


# ---------------------------------------------------------------- end-to-end with a fake ISC
class FakeISC:
    """Minimal in-memory stand-in for ISCClient (just what Onboarder calls)."""
    COLLECTIONS = ("/v3/sources", "/v3/access-profiles", "/beta/source-apps", "/beta/workgroups")
    CANNED = {
        ("GET", "/v3/public-identities"): [{"id": "u1", "name": "Owner"}],
        ("GET", "/v3/sources/id1/schemas"): [{"id": "sc1", "name": "account", "nativeObjectType": "account"}],
        ("GET", "/beta/sources/id1/correlation-config"): {},
        ("POST", "/v2024/sources/id1/load-accounts"): {"task": {"id": "t1"}},
        ("GET", "/beta/task-status/t1"): {"completed": True, "completionStatus": "SUCCESS"},
    }

    def __init__(self, dry_run=False, fail_on=None, fail_text="boom"):
        self.dry_run, self.fail_on, self.fail_text = dry_run, fail_on, fail_text
        self.items = {c: [] for c in self.COLLECTIONS}

    def request(self, method, path, *, params=None, json_body=None, write=False, **_):
        if write and self.dry_run:
            return None
        if (method, path) == self.fail_on:
            raise ISCError(f"{method} {path} -> 500: {self.fail_text}")
        coll = path.removesuffix("/all")
        if method == "POST" and coll in self.items:
            item = {**json_body, "id": f"id{len(self.items[coll]) + 1}"}
            self.items[coll].append(item)
            return item
        if method == "GET" and coll in self.items:
            name = re.search(r'"(.*)"', params["filters"]).group(1)
            return [i for i in self.items[coll] if i["name"] == name]
        return self.CANNED.get((method, path))

    def patch(self, path, ops, write=True):
        return None

    def get_all(self, path, params=None):
        return [{"id": "e1", "value": "Approver", "requestable": False}] if path == "/beta/entitlements" else []


@pytest.fixture
def app(tmp_path):
    csv_file = tmp_path / "accounts.csv"
    csv_file.write_text("EmployeeID,UserName,Role\n1,jane,Approver\n")
    return {
        "appName": "Test-App", "connectorType": "delimited", "owner": "owner.alias",
        "application": "Test App", "reapplyConfig": True,
        "delimited": {"accountFile": str(csv_file),
                      "schema": {"identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                                 "entitlementColumns": ["Role"]},
                      "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]},
        "accessProfiles": [{"name": "Test App - Approver", "entitlementValues": ["Approver"]}],
    }


DEFAULTS = {"connectors": {"delimited": "delimited-file"}, "delimitedConnectorAttributes": {}}


def run_app(runlog, isc, app):
    results = [onboard.Onboarder(isc, DEFAULTS, runlog).run(app)]
    runlog.end(results)
    return results, read_events(runlog)


def test_success_run_logs_every_generation_step(runlog_factory, app):
    results, events = run_app(runlog_factory(target="sandbox"), FakeISC(), app)
    assert results[0]["status"] == "ok"
    names = [e["event"] for e in events]
    for expected in ("run_start", "app_start", "source_created", "schema_applied", "correlation_applied",
                     "aggregation_done", "entitlements_requestable", "application_created",
                     "access_profile_created", "access_profile_attached", "app_done", "run_end"):
        assert expected in names, expected
    assert names[0] == "run_start" and names[-1] == "run_end"
    assert all(e["app"] == "Test-App" for e in events if e["event"] in ("source_created", "app_done"))
    end = events[-1]
    assert end["counts"] == {"ok": 1} and end["apps"] == {"Test-App": "ok"} and end["level"] == "INFO"


def test_dry_run_marks_planned_writes(runlog_factory, app):
    _, events = run_app(runlog_factory(dry_run=True), FakeISC(dry_run=True), app)
    assert all(e["dry_run"] for e in events)
    assert "source_created" in [e["event"] for e in events]


def test_failed_api_call_is_logged_and_run_continues(runlog_factory, app):
    isc = FakeISC(fail_on=("PUT", "/v3/sources/id1/schemas/sc1"),
                  fail_text=f"upstream said client_secret={SECRET} contact a.b@corp.io")
    results, events = run_app(runlog_factory(), isc, app)
    assert results[0]["status"] == "failed"
    failed = next(e for e in events if e["event"] == "app_failed")
    assert failed["level"] == "ERROR" and failed["app"] == "Test-App" and "Traceback" in failed["traceback"]
    assert "app_done" not in [e["event"] for e in events]
    end = events[-1]
    assert end["counts"] == {"failed": 1} and end["level"] == "ERROR"


def test_partial_run_when_access_profile_creation_fails(runlog_factory, app):
    results, events = run_app(runlog_factory(), FakeISC(fail_on=("POST", "/v3/access-profiles")), app)
    assert results[0]["status"] == "partial"
    assert "access_profiles_failed" in [e["event"] for e in events]
    assert events[-1]["counts"] == {"partial": 1}


def test_secret_never_reaches_the_log_file(runlog_factory, app):
    text_with_secrets = (f"client_secret={SECRET} Authorization: Bearer abcdefgh12345678 "
                         f"password=hunter22 mail jane.doe@corp.io {SECRET}")
    rl = runlog_factory()
    isc = FakeISC(fail_on=("PUT", "/v3/sources/id1/schemas/sc1"), fail_text=text_with_secrets)
    onboard.Onboarder(isc, DEFAULTS, rl).run(app)
    rl.event("api_error", "ERROR", body=text_with_secrets, message=text_with_secrets)
    rl.failure("run_crashed", RuntimeError(text_with_secrets))
    rl.end([])
    text = rl.path.read_text(encoding="utf-8")
    for leaked in (SECRET, "abcdefgh12345678", "hunter22", "jane.doe@corp.io"):
        assert leaked not in text, leaked
    assert "j***@corp.io" in text
