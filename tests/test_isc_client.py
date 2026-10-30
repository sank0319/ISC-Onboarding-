"""ISCClient against a scripted requests session: retry, re-auth, errors and dry-run."""
import io
import json
from types import SimpleNamespace

import pytest
import requests

import isc_client
from isc_client import ISCClient, ISCError


def resp(code=200, body=None, headers=None, ctype="application/json", text=None):
    raw = text if text is not None else (json.dumps(body) if body is not None else "")
    r = SimpleNamespace(status_code=code, headers={"Content-Type": ctype, **(headers or {})}, ok=code < 400,
                        content=raw.encode(), text=raw)
    r.json = lambda: json.loads(raw)
    return r


class Session:
    def __init__(self, script):
        self.script, self.calls, self.headers = list(script), [], {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, {**kw, "params": dict(kw.get("params") or {})}))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture
def make(monkeypatch):
    tokens = []

    def post(url, data=None, timeout=None):
        tokens.append(url)
        return resp(200, {"access_token": f"tok{len(tokens)}", "expires_in": 700})

    monkeypatch.setattr(isc_client.requests, "post", post)

    def build(*script, dry_run=False):
        c = ISCClient("t", "id", "secret", dry_run=dry_run, base_url="https://isc.invalid/")
        c.session, c.tokens = Session(script), tokens
        return c
    return build


def test_success_returns_json(make):
    assert make(resp(200, {"a": 1})).request("GET", "/x") == {"a": 1}


@pytest.mark.parametrize("retry_after,expected", [("3", [3]), ("0", [0]), ("", [1]), ("abc", [1]),
                                                  ("Wed, 21 Oct 2026 07:28:00 GMT", [1]), (None, [1])])
def test_429_honours_retry_after(make, clock, retry_after, expected):
    hdr = {} if retry_after is None else {"Retry-After": retry_after}
    c = make(resp(429, headers=hdr), resp(200, {"ok": 1}))
    assert c.request("GET", "/x") == {"ok": 1} and clock.sleeps == expected


@pytest.mark.parametrize("retry_after", ["-5", "-1"])
def test_negative_retry_after_does_not_crash(make, clock, retry_after):
    c = make(resp(429, headers={"Retry-After": retry_after}), resp(200, {"ok": 1}))
    assert c.request("GET", "/x") == {"ok": 1} and all(s >= 0 for s in clock.sleeps)


def test_huge_retry_after_is_capped(make, clock):
    make(resp(429, headers={"Retry-After": "86400"}), resp(200, {})).request("GET", "/x")
    assert clock.sleeps[0] <= 300


def test_backoff_grows_exponentially_and_is_capped(make, clock):
    c = make(*[resp(503)] * 8, resp(200, {}))
    c.request("GET", "/x", retries=9)
    assert clock.sleeps == [1, 2, 4, 8, 16, 32, 60, 60]


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_5xx_exhausted_raises_isc_error(make, code):
    c = make(*[resp(code)] * 3)
    with pytest.raises(ISCError, match="failed after 3 attempts"):
        c.request("GET", "/x", retries=3)
    assert len(c.session.calls) == 3


@pytest.mark.parametrize("code", [500, 503, 429])
def test_5xx_then_success(make, code):
    assert make(resp(code), resp(code), resp(200, {"ok": 1})).request("GET", "/x") == {"ok": 1}


@pytest.mark.parametrize("code", [400, 403, 404, 409, 422])
def test_non_retryable_4xx_raises_immediately(make, clock, code):
    c = make(resp(code, text="x" * 5000), resp(200, {}))
    with pytest.raises(ISCError, match=str(code)) as e:
        c.request("POST", "/x", write=True)
    assert len(c.session.calls) == 1 and not clock.sleeps and len(str(e.value)) < 1100


def test_401_reauthenticates_once_then_succeeds(make):
    c = make(resp(401), resp(200, {"ok": 1}))
    assert c.request("GET", "/x") == {"ok": 1} and len(c.tokens) == 2


def test_second_401_raises_without_looping(make):
    c = make(resp(401), resp(401), resp(200, {}))
    with pytest.raises(ISCError, match="401"):
        c.request("GET", "/x")
    assert len(c.session.calls) == 2 and len(c.tokens) == 2


@pytest.mark.parametrize("exc", [requests.Timeout("t"), requests.ConnectionError("reset")], ids=type)
def test_network_errors_are_retried(make, exc):
    assert make(exc, resp(200, {"ok": 1})).request("GET", "/x") == {"ok": 1}


def test_network_errors_exhausted_raise_isc_error(make):
    c = make(*[requests.Timeout("t")] * 2)
    with pytest.raises(ISCError):
        c.request("GET", "/x", retries=2)


def test_malformed_json_raises_isc_error(make):
    with pytest.raises(ISCError, match="JSON"):
        make(resp(200, text="<html>gateway</html>")).request("GET", "/x")


@pytest.mark.parametrize("r", [resp(204, ctype=""), resp(200, text="ok", ctype="text/plain"), resp(200, ctype="application/json")],
                         ids=["204", "text", "empty-json"])
def test_empty_or_non_json_body_returns_none(make, r):
    assert make(r).request("GET", "/x") is None


def test_file_handle_is_rewound_on_retry(make):
    seen = []
    c = make(resp(503), resp(200, {}))
    original = c.session.request
    c.session.request = lambda *a, **kw: (seen.append(kw["files"]["file"][1].read()), original(*a, **kw))[1]
    c.request("POST", "/x", files={"file": ("a.csv", io.BytesIO(b"abc"), "text/csv")}, write=True)
    assert seen == [b"abc", b"abc"]


# ---- dry-run
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_dry_run_blocks_writes_before_auth_or_network(make, method):
    c = make(dry_run=True)
    assert c.request(method, "/x", json_body={"a": 1}, write=True) is None
    assert c.patch("/x", [{"op": "replace", "path": "/a", "value": 1}]) is None
    assert not c.session.calls and not c.tokens


def test_dry_run_still_reads(make):
    c = make(resp(200, [1]), dry_run=True)
    assert c.request("GET", "/x") == [1]


def test_patch_sends_json_patch_content_type(make):
    c = make(resp(200, {}))
    c.patch("/x", [{"op": "add", "path": "/a", "value": 1}])
    assert c.session.calls[0][2]["headers"]["Content-Type"] == "application/json-patch+json"


# ---- auth
@pytest.mark.parametrize("token_resp", [resp(401, text="bad"), resp(500, text="boom"), resp(200, {"expires_in": 1}),
                                        resp(200, text="not json")], ids=["401", "500", "no-token", "html"])
def test_token_failures_raise_isc_error(make, monkeypatch, token_resp):
    monkeypatch.setattr(isc_client.requests, "post", lambda *a, **k: token_resp)
    with pytest.raises(ISCError):
        make(resp(200, {})).request("GET", "/x")


def test_token_is_reused_until_near_expiry(make, clock):
    c = make(resp(200, {}), resp(200, {}), resp(200, {}))
    c.request("GET", "/x"), c.request("GET", "/x")
    assert len(c.tokens) == 1
    clock.now += 700
    c.request("GET", "/x")
    assert len(c.tokens) == 2


# ---- paging
def pages(*sizes):
    return [resp(200, [{"i": n} for n in range(s)]) for s in sizes]


@pytest.mark.parametrize("sizes,calls", [((0,), 1), ((5,), 1), ((250, 0), 2), ((250, 250, 10), 3)])
def test_get_all_paging(make, sizes, calls):
    c = make(*pages(*sizes))
    assert len(c.get_all("/x")) == sum(sizes) and len(c.session.calls) == calls
    assert [k["params"]["offset"] for _, _, k in c.session.calls] == [250 * i for i in range(calls)]


def test_get_all_with_empty_body(make):
    assert make(resp(204, ctype="")).get_all("/x") == []
