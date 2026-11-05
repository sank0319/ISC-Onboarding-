"""secrets_store: Vault (GitHub OIDC -> JWT login -> KV v2) and env fallback, fully offline."""
import base64
import json

import pytest
import requests

import onboard
import secrets_store as ss
from secrets_store import SecretsError, load_credentials

JWT_BODY = base64.urlsafe_b64encode(json.dumps(
    {"iss": "https://token.actions.githubusercontent.com", "sub": "repo:o@1/r@2:environment:sandbox",
     "aud": "vault-isc-onboarding", "secret_looking": "nope"}).encode()).decode().rstrip("=")
JWT = f"eyJhbGciOiJSUzI1NiJ9.{JWT_BODY}.c2ln"
REQ_TOKEN = "gha-request-token-ZZZ"
CLIENT_TOKEN = "hvs.CLIENT-TOKEN-123"
SECRET = {"ISC_TENANT": "acme-sb", "ISC_CLIENT_ID": "cid-1234567890", "ISC_CLIENT_SECRET": "sec-VALUE-98765"}
OIDC_URL = "https://pipelines.actions.githubusercontent.com/abc/idtoken?api-version=2.0"


class Resp:
    def __init__(self, status=200, body=None, raw=None):
        self.status_code, self.body, self.raw = status, body, raw

    def json(self):
        if self.raw is not None:
            raise ValueError("bad json")
        return self.body


class FakeSession:
    """Records every call; replies by (METHOD, url-suffix). A Resp, or an Exception to raise."""

    def __init__(self, **over):
        self.calls = []
        self.routes = {
            ("GET", "idtoken"): Resp(200, {"value": JWT}),
            ("POST", "/login"): Resp(200, {"auth": {"client_token": CLIENT_TOKEN, "lease_duration": 60}}),
            ("GET", "/data/"): Resp(200, {"data": {"data": dict(SECRET), "metadata": {"version": 3}}}),
            ("POST", "revoke-self"): Resp(204, None),
        }
        for k, v in over.items():
            self.routes[tuple(k.split("__", 1))] = v

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, frag), r in self.routes.items():
            if m == method and frag in url:
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def post(self, url, **kw):
        return self._do("POST", url, **kw)

    def find(self, method, frag):
        return [c for c in self.calls if c[0] == method and frag in c[1]]


def venv(**extra):
    e = {"VAULT_ADDR": "https://vault.example.com:8200", "VAULT_ROLE": "isc-sandbox",
         "VAULT_SECRET_PATH": "isc/sandbox", "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
         "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQ_TOKEN}
    e.update(extra)
    return {k: v for k, v in e.items() if v is not None}


# ---------------- happy path
def test_vault_happy_path_exact_requests():
    s = FakeSession()
    creds, source = load_credentials(venv(VAULT_NAMESPACE="admin"), s)
    assert source == "hashicorp-vault" and creds == SECRET
    (m1, u1, k1), (m2, u2, k2), (m3, u3, k3), (m4, u4, k4) = s.calls
    assert (m1, u1) == ("GET", OIDC_URL)
    assert k1["params"] == {"audience": ss.DEFAULT_AUDIENCE}
    assert k1["headers"] == {"Authorization": f"Bearer {REQ_TOKEN}"}
    assert (m2, u2) == ("POST", "https://vault.example.com:8200/v1/auth/jwt/login")
    assert k2["json"] == {"role": "isc-sandbox", "jwt": JWT} and k2["headers"] == {"X-Vault-Namespace": "admin"}
    assert (m3, u3) == ("GET", "https://vault.example.com:8200/v1/secret/data/isc/sandbox")
    assert k3["headers"] == {"X-Vault-Token": CLIENT_TOKEN, "X-Vault-Namespace": "admin"}
    assert (m4, u4) == ("POST", "https://vault.example.com:8200/v1/auth/token/revoke-self")
    assert k4["headers"]["X-Vault-Token"] == CLIENT_TOKEN
    assert all(k["timeout"] == ss.TIMEOUT and k["allow_redirects"] is False for _, _, k in s.calls)


def test_custom_mounts_audience_no_namespace_header():
    s = FakeSession()
    load_credentials(venv(VAULT_AUTH_MOUNT="gha/jwt", VAULT_KV_MOUNT="kv", VAULT_AUDIENCE="https://vault.corp"), s)
    assert s.calls[0][2]["params"] == {"audience": "https://vault.corp"}
    assert s.calls[1][1].endswith("/v1/auth/gha/jwt/login") and s.calls[2][1].endswith("/v1/kv/data/isc/sandbox")
    assert all("X-Vault-Namespace" not in k.get("headers", {}) for _, _, k in s.calls)


def test_empty_optional_settings_use_defaults():
    s = FakeSession()
    load_credentials(venv(VAULT_NAMESPACE="", VAULT_AUTH_MOUNT="", VAULT_KV_MOUNT=""), s)  # unset GitHub vars are ""
    assert s.calls[1][1].endswith("/v1/auth/jwt/login") and s.calls[2][1].endswith("/v1/secret/data/isc/sandbox")


def test_optional_base_url_present_and_absent():
    s = FakeSession(**{"GET__/data/": Resp(200, {"data": {"data": {**SECRET, "ISC_BASE_URL": "https://x.example"}}})})
    assert load_credentials(venv(), s)[0]["ISC_BASE_URL"] == "https://x.example"
    assert "ISC_BASE_URL" not in load_credentials(venv(), FakeSession())[0]


# ---------------- env fallback
def test_env_fallback():
    env = dict(SECRET, ISC_BASE_URL="https://b.example")
    creds, source = load_credentials(env, FakeSession())
    assert source == "env" and creds == env


def test_env_fallback_missing_names_listed():
    with pytest.raises(SecretsError) as e:
        load_credentials({"ISC_TENANT": "t"}, FakeSession())
    msg = str(e.value)
    assert "ISC_CLIENT_ID" in msg and "ISC_CLIENT_SECRET" in msg and "VAULT_ADDR" in msg and "ISC_TENANT," not in msg


def test_env_fallback_blank_vault_addr_is_env_mode():
    assert load_credentials(dict(SECRET, VAULT_ADDR="  "), FakeSession())[1] == "env"


def test_vault_wins_over_stale_env():
    s = FakeSession()
    creds, source = load_credentials(venv(ISC_TENANT="stale", ISC_CLIENT_ID="stale", ISC_CLIENT_SECRET="stale",
                                          ISC_BASE_URL="https://stale"), s)
    assert source == "hashicorp-vault" and creds == SECRET


def test_vault_mode_never_falls_back_to_env_on_failure():
    s = FakeSession(**{"POST__/login": Resp(403, {"errors": ["permission denied"]})})
    with pytest.raises(SecretsError):
        load_credentials(venv(**SECRET), s)


# ---------------- validation
@pytest.mark.parametrize("name,bad", [
    ("VAULT_SECRET_PATH", "../sys/policy"), ("VAULT_SECRET_PATH", "isc/../x"), ("VAULT_SECRET_PATH", "/isc/x"),
    ("VAULT_SECRET_PATH", "isc//x"), ("VAULT_SECRET_PATH", "isc/x/"), ("VAULT_SECRET_PATH", "isc/x?list=true"),
    ("VAULT_SECRET_PATH", "isc/x#f"), ("VAULT_SECRET_PATH", "isc/%2e%2e/x"), ("VAULT_SECRET_PATH", "isc/x y"),
    ("VAULT_SECRET_PATH", "isc/\nx"), ("VAULT_SECRET_PATH", "isc/.hidden"), ("VAULT_SECRET_PATH", "isc\\x"),
    ("VAULT_KV_MOUNT", ".."), ("VAULT_KV_MOUNT", "secret/../sys"), ("VAULT_KV_MOUNT", "a?b"), ("VAULT_KV_MOUNT", "/secret"),
    ("VAULT_AUTH_MOUNT", "jwt/../x"), ("VAULT_AUTH_MOUNT", "jwt%2f"), ("VAULT_AUTH_MOUNT", "jwt login"),
    ("VAULT_ROLE", "r/x"), ("VAULT_ROLE", "r x"), ("VAULT_ROLE", "r\nx"), ("VAULT_ROLE", "a" * 129), ("VAULT_ROLE", "../r"),
    ("VAULT_NAMESPACE", "admin\r\nX-Evil: 1"), ("VAULT_NAMESPACE", "../admin"), ("VAULT_NAMESPACE", "a b"),
    ("VAULT_AUDIENCE", "a b"), ("VAULT_AUDIENCE", "a&b=c"), ("VAULT_AUDIENCE", "a\nb"),
])
def test_invalid_values_rejected_before_any_request(name, bad):
    s = FakeSession()
    with pytest.raises(SecretsError, match=name):
        load_credentials(venv(**{name: bad}), s)
    assert s.calls == []


@pytest.mark.parametrize("name,ok", [
    ("VAULT_SECRET_PATH", "isc/sandbox"), ("VAULT_SECRET_PATH", "team_a/isc-prod/v2"),
    ("VAULT_KV_MOUNT", "kv-isc"), ("VAULT_KV_MOUNT", "team/kv"), ("VAULT_AUTH_MOUNT", "github_jwt"),
    ("VAULT_ROLE", "isc.sandbox-ro_1"), ("VAULT_NAMESPACE", "admin"), ("VAULT_NAMESPACE", "admin/team-a"),
    ("VAULT_AUDIENCE", "https://github.com/my-org")])
def test_valid_values_accepted(name, ok):
    assert load_credentials(venv(**{name: ok}), FakeSession())[1] == "hashicorp-vault"


def test_vault_addr_requires_role_and_path():
    with pytest.raises(SecretsError, match="VAULT_ROLE and VAULT_SECRET_PATH"):
        load_credentials({"VAULT_ADDR": "https://v.example.com"}, FakeSession())


@pytest.mark.parametrize("addr", [
    "http://vault.example.com:8200", "ftp://vault.example.com", "vault.example.com:8200", "//vault.example.com",
    "https://", "http://localhost.evil.com", "http://127.0.0.1.evil.com", "https://user:pw@vault.example.com",
    "https://vault.example.com/v1", "https://vault.example.com?x=1", "https://vault.example.com#f",
    "https://vault.example.com:notaport", "https://vault.example.com /x", "https://vault.example.com\n/x",
])
def test_vault_addr_rejected(addr):
    s = FakeSession()
    with pytest.raises(SecretsError, match="VAULT_ADDR"):
        load_credentials(venv(VAULT_ADDR=addr), s)
    assert s.calls == []


@pytest.mark.parametrize("addr,expected", [
    ("http://localhost:8200", "http://localhost:8200"), ("http://127.0.0.1:8200/", "http://127.0.0.1:8200"),
    ("https://vault.example.com/", "https://vault.example.com"),
    ("https://abc.hashicorp.cloud:8200", "https://abc.hashicorp.cloud:8200")])
def test_vault_addr_accepted(addr, expected):
    s = FakeSession()
    load_credentials(venv(VAULT_ADDR=addr), s)
    assert s.calls[1][1].startswith(expected + "/v1/auth/")


def test_oidc_env_missing_or_not_https():
    with pytest.raises(SecretsError, match="id-token: write"):
        load_credentials(venv(ACTIONS_ID_TOKEN_REQUEST_URL=None, ACTIONS_ID_TOKEN_REQUEST_TOKEN=None), FakeSession())
    with pytest.raises(SecretsError, match="https"):
        load_credentials(venv(ACTIONS_ID_TOKEN_REQUEST_URL="http://evil.example/idtoken"), FakeSession())


# ---------------- failures
def msg_of(env, session):
    with pytest.raises(SecretsError) as e:
        load_credentials(env, session)
    return str(e.value)


@pytest.mark.parametrize("route,status,needle", [
    ("POST__/login", 403, "HTTP 403"), ("GET__/data/", 403, "policy"), ("GET__/data/", 404, "not found"),
    ("GET__/data/", 500, "HTTP 500"), ("GET__/data/", 503, "HTTP 503"), ("POST__/login", 400, "HTTP 400"),
    ("GET__idtoken", 401, "HTTP 401"), ("GET__/data/", 302, "HTTP 302")])
def test_http_errors(route, status, needle):
    s = FakeSession(**{route: Resp(status, {"errors": ["permission denied"]})})
    assert needle in msg_of(venv(), s)


def test_vault_errors_text_shown_but_scrubbed():
    body = {"errors": [f"bad {JWT} and {CLIENT_TOKEN} {REQ_TOKEN} " + "x" * 500]}
    m = msg_of(venv(), FakeSession(**{"POST__/login": Resp(400, body)}))
    assert "HTTP 400" in m and "bad" in m and len(m) < 700
    assert JWT not in m and REQ_TOKEN not in m and "eyJ" not in m


def test_vault_error_without_errors_field():
    m = msg_of(venv(), FakeSession(**{"POST__/login": Resp(502, raw="<html>")}))
    assert "HTTP 502" in m


@pytest.mark.parametrize("route", ["GET__idtoken", "POST__/login", "GET__/data/"])
def test_timeouts_and_network_errors(route):
    url_secret = "https://vault.example.com:8200/v1/x?token=SEKRET"
    m = msg_of(venv(), FakeSession(**{route: requests.exceptions.ConnectTimeout(url_secret)}))
    assert "timed out" in m and "SEKRET" not in m and "http" not in m
    m = msg_of(venv(), FakeSession(**{route: requests.exceptions.SSLError(url_secret)}))
    assert "SSLError" in m and "SEKRET" not in m and "http" not in m
    m = msg_of(venv(), FakeSession(**{route: requests.exceptions.ConnectionError(url_secret)}))
    assert "ConnectionError" in m and "SEKRET" not in m


@pytest.mark.parametrize("route", ["GET__idtoken", "POST__/login", "GET__/data/"])
def test_malformed_json(route):
    assert "not valid JSON" in msg_of(venv(), FakeSession(**{route: Resp(200, raw="<html>")}))


@pytest.mark.parametrize("route,body", [
    ("GET__idtoken", {}), ("GET__idtoken", {"value": ""}), ("GET__idtoken", []),
    ("POST__/login", {}), ("POST__/login", {"auth": None}), ("POST__/login", {"auth": {"client_token": ""}}),
    ("GET__/data/", {}), ("GET__/data/", {"data": {"metadata": {}}}), ("GET__/data/", {"data": {"data": "x"}}),
    ("GET__/data/", {"data": {"data": {"ISC_CLIENT_ID": "x"}}}),  # a KV v1-ish/odd shape
])
def test_unexpected_shapes(route, body):
    msg_of(venv(), FakeSession(**{route: Resp(200, body)}))


def test_missing_and_empty_keys_named_without_values():
    data = {"ISC_TENANT": "t", "ISC_CLIENT_ID": "  ", "ISC_CLIENT_SECRET": 12345}
    m = msg_of(venv(), FakeSession(**{"GET__/data/": Resp(200, {"data": {"data": data}})}))
    assert "ISC_CLIENT_ID" in m and "ISC_CLIENT_SECRET" in m and "ISC_TENANT" not in m and "12345" not in m
    assert "isc/sandbox" in m


def test_empty_secret():
    m = msg_of(venv(), FakeSession(**{"GET__/data/": Resp(200, {"data": {"data": {}}})}))
    assert all(k in m for k in ss.REQUIRED_KEYS)


def test_secret_values_never_in_error_messages():
    data = {"ISC_TENANT": "t", "ISC_CLIENT_ID": "cid-1234567890", "ISC_CLIENT_SECRET": ""}
    m = msg_of(venv(), FakeSession(**{"GET__/data/": Resp(200, {"data": {"data": data}})}))
    assert "cid-1234567890" not in m


def test_tokens_and_jwt_never_in_any_error():
    cases = [("POST__/login", Resp(403, {"errors": [f"{JWT} {CLIENT_TOKEN}"]})),
             ("GET__/data/", Resp(403, {"errors": [f"{JWT} {CLIENT_TOKEN}"]})),
             ("GET__/data/", Resp(500, {"errors": [CLIENT_TOKEN]})),
             ("GET__/data/", requests.exceptions.ConnectionError(f"{CLIENT_TOKEN} {JWT}")),
             ("POST__/login", Resp(200, raw="x"))]
    for route, r in cases:
        m = msg_of(venv(), FakeSession(**{route: r}))
        assert JWT not in m and CLIENT_TOKEN not in m and REQ_TOKEN not in m, route


def test_no_exception_chain_leaks_network_message():
    s = FakeSession(**{"POST__/login": requests.exceptions.ConnectionError(f"url?jwt={JWT}")})
    with pytest.raises(SecretsError) as e:
        load_credentials(venv(), s)
    assert e.value.__cause__ is None and e.value.__context__ is None


# ---------------- masks
def test_add_mask_only_in_github_actions(capsys):
    load_credentials(venv(), FakeSession())
    assert "::add-mask::" not in capsys.readouterr().out
    load_credentials(venv(GITHUB_ACTIONS="true"), FakeSession())
    out = capsys.readouterr().out.splitlines()
    for v in (SECRET["ISC_CLIENT_ID"], SECRET["ISC_CLIENT_SECRET"], JWT, CLIENT_TOKEN, REQ_TOKEN):
        assert f"::add-mask::{v}" in out
    assert f"::add-mask::{SECRET['ISC_TENANT']}" not in out


def test_add_mask_env_mode_and_multiline(capsys):
    load_credentials(dict(SECRET, GITHUB_ACTIONS="true", ISC_CLIENT_SECRET="line1\nline2"), FakeSession())
    out = capsys.readouterr().out.splitlines()
    assert "::add-mask::line1" in out and "::add-mask::line2" in out
    load_credentials(dict(SECRET), FakeSession())
    assert capsys.readouterr().out == ""


def test_jwt_masked_even_if_login_fails(capsys):
    msg_of(venv(GITHUB_ACTIONS="true"), FakeSession(**{"POST__/login": Resp(403, {"errors": ["x"]})}))
    assert f"::add-mask::{JWT}" in capsys.readouterr().out


# ---------------- revoke
def test_revoke_called_after_read():
    s = FakeSession()
    load_credentials(venv(), s)
    assert len(s.find("POST", "revoke-self")) == 1 and s.calls[-1][0] == "POST"


def test_revoke_failure_ignored():
    for err in (Resp(500, {"errors": ["boom"]}), requests.exceptions.ConnectionError("x"), RuntimeError("y")):
        assert load_credentials(venv(), FakeSession(**{"POST__revoke-self": err}))[0] == SECRET


def test_revoke_also_when_secret_read_fails():
    s = FakeSession(**{"GET__/data/": Resp(403, {"errors": ["denied"]})})
    msg_of(venv(), s)
    assert len(s.find("POST", "revoke-self")) == 1


def test_no_revoke_when_login_fails():
    s = FakeSession(**{"POST__/login": Resp(403, {"errors": ["denied"]})})
    msg_of(venv(), s)
    assert s.find("POST", "revoke-self") == []


# ---------------- --claims
def test_claims_prints_only_iss_sub_aud(capsys):
    s = FakeSession()
    rc = ss.main(["--claims"], venv(), s)
    out = capsys.readouterr().out
    assert rc == 0 and out.splitlines() == [
        "iss: https://token.actions.githubusercontent.com", "sub: repo:o@1/r@2:environment:sandbox",
        "aud: vault-isc-onboarding"]
    assert "secret_looking" not in out and JWT not in out and "nope" not in out
    assert [c[0] for c in s.calls] == ["GET"]  # does not talk to Vault


def test_claims_masks_jwt_in_actions(capsys):
    ss.main(["--claims"], venv(GITHUB_ACTIONS="true"), FakeSession())
    assert f"::add-mask::{JWT}" in capsys.readouterr().out


def test_claims_errors(capsys):
    assert ss.main(["--claims"], {}, FakeSession()) == 1
    assert "id-token: write" in capsys.readouterr().err
    s = FakeSession(**{"GET__idtoken": Resp(200, {"value": "not-a-jwt"})})
    assert ss.main(["--claims"], venv(), s) == 1
    assert "decodable" in capsys.readouterr().err


def test_main_requires_flag():
    with pytest.raises(SystemExit):
        ss.main([], {}, FakeSession())


# ---------------- end to end through onboard.main()
def test_onboard_main_uses_vault_creds_and_log_has_no_secrets(tmp_path, monkeypatch, isc, defaults, csv_file):
    import yaml
    seen = {}

    def fake_client(*a, dry_run=False, **k):
        seen.update(args=a, kwargs=k)
        isc.dry_run = dry_run
        return isc

    monkeypatch.setattr(onboard, "ISCClient", fake_client)
    fake = FakeSession(**{"GET__/data/": Resp(200, {"data": {"data": dict(
        SECRET, ISC_BASE_URL="https://custom.example")}})})
    real = ss.requests.Session
    monkeypatch.setattr(ss.requests, "Session", lambda: fake)
    for k, v in venv(VAULT_NAMESPACE="admin", ISC_CLIENT_SECRET="stale-env-secret").items():
        monkeypatch.setenv(k, v)
    app = {"appName": "E2e-App", "connectorType": "delimited", "owner": "asmith", "application": "E2E App",
           "delimited": {"accountFile": csv_file(),
                         "schema": {"identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                                    "entitlementColumns": ["Role"]},
                         "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]},
           "accessProfiles": [{"name": "E2E AP", "entitlementValues": ["Approver"]}]}
    (tmp_path / "app_e2e.yaml").write_text(yaml.safe_dump({"apps": [app]}))
    (tmp_path / "defaults.yaml").write_text(yaml.safe_dump(defaults))
    monkeypatch.setattr("sys.argv", ["onboard.py", "--apps", str(tmp_path / "app_*.yaml"), "--defaults",
                                     str(tmp_path / "defaults.yaml"), "--report", str(tmp_path / "r.json")])
    with pytest.raises(SystemExit) as e:
        onboard.main()
    assert e.value.code == 0 and real is not ss.requests.Session
    assert seen["args"] == ("acme-sb", SECRET["ISC_CLIENT_ID"], SECRET["ISC_CLIENT_SECRET"])
    assert seen["kwargs"]["base_url"] == "https://custom.example"

    logs = list((tmp_path / "logs").rglob("*.jsonl"))
    assert logs
    text = "\n".join(p.read_text() for p in logs)
    events = [json.loads(line) for line in text.splitlines()]
    loaded = [ev for ev in events if ev["event"] == "secrets_loaded"]
    assert loaded and loaded[0]["source"] == "hashicorp-vault" and loaded[0]["vault_path"] == "isc/sandbox"
    for secret in (SECRET["ISC_CLIENT_ID"], SECRET["ISC_CLIENT_SECRET"], JWT, CLIENT_TOKEN, REQ_TOKEN, "stale-env-secret"):
        assert secret not in text


def test_onboard_main_credential_failure_is_logged_as_run_crashed(tmp_path, monkeypatch, defaults, csv_file):
    import yaml
    app = {"appName": "E2e-App", "connectorType": "delimited", "owner": "asmith", "application": "E2E App",
           "delimited": {"accountFile": csv_file(),
                         "schema": {"identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                                    "entitlementColumns": ["Role"]},
                         "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]},
           "accessProfiles": [{"name": "E2E AP", "entitlementValues": ["Approver"]}]}
    (tmp_path / "app_e2e.yaml").write_text(yaml.safe_dump({"apps": [app]}))
    (tmp_path / "defaults.yaml").write_text(yaml.safe_dump(defaults))
    monkeypatch.setattr("sys.argv", ["onboard.py", "--apps", str(tmp_path / "app_*.yaml"), "--defaults",
                                     str(tmp_path / "defaults.yaml"), "--report", str(tmp_path / "r.json")])
    with pytest.raises(SecretsError, match="No credentials found"):
        onboard.main()
    text = "\n".join(p.read_text() for p in (tmp_path / "logs").rglob("*.jsonl"))
    assert "run_crashed" in text


# ---------------- connectivity_check
def test_connectivity_check_prints_only_names(monkeypatch, capsys, isc):
    import connectivity_check as cc
    for k, v in SECRET.items():
        monkeypatch.setenv(k, v)
    seen = {}

    class Client:
        base = "https://acme-sb.api.identitynow.com"

        def __init__(self, *a, **k):
            seen["args"] = a

        def request(self, method, path, params=None):
            assert (method, path, params) == ("GET", "/v3/sources", {"limit": 5})
            return [{"name": "Src A", "id": "1", "connectorAttributes": {"password": "p"}}, {"name": "Src B"}]

    monkeypatch.setattr(cc, "ISCClient", Client)
    assert cc.main() == 0
    out = capsys.readouterr().out
    assert "Src A" in out and "Src B" in out and SECRET["ISC_CLIENT_SECRET"] not in out and "password" not in out


def test_connectivity_check_errors_exit_nonzero(monkeypatch, capsys):
    import connectivity_check as cc
    from isc_client import ISCError
    assert cc.main() == 1 and "credentials" in capsys.readouterr().err
    for k, v in SECRET.items():
        monkeypatch.setenv(k, v)

    class Boom:
        base = "b"

        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise ISCError("GET /v3/sources -> 401")

    monkeypatch.setattr(cc, "ISCClient", Boom)
    assert cc.main() == 1 and "401" in capsys.readouterr().err
