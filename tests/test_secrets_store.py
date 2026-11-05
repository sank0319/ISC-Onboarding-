"""Credential loading: Azure Key Vault via GitHub OIDC, env fallback, and that nothing secret leaks."""
import json
import os
from pathlib import Path

import pytest
import yaml
import requests

import onboard
import secrets_store as ss
from run_log import redact

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GH_JWT = "eyJhbGciOi.GITHUBPAYLOAD.sig"
KV_TOKEN = "kv-access-token-123456"
VALUES = {"isc-tenant": "acme", "isc-client-id": "isc-id-9999", "isc-client-secret": "isc-secret-8888",
          "isc-base-url": "https://acme.api.example.com"}

VAULT_ENV = {"AZURE_KEY_VAULT_NAME": "kv-isc-sandbox", "AZURE_TENANT_ID": TENANT, "AZURE_CLIENT_ID": CLIENT,
             "ACTIONS_ID_TOKEN_REQUEST_URL": "https://actions.example/token?x=1",
             "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-bearer"}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.content = b"x" if body is not None else b""
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._body


class FakeSession:
    """Plays GitHub's OIDC endpoint, Entra and Key Vault; records every call."""

    def __init__(self, secrets=None, github=None, entra=None, vault_status=None, boom=None):
        self.secrets = dict(VALUES if secrets is None else secrets)
        self.github = github or Resp(200, {"value": GH_JWT})
        self.entra = entra or Resp(200, {"access_token": KV_TOKEN})
        self.vault_status = vault_status or {}
        self.boom = boom
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if self.boom:
            raise self.boom
        if "actions.example" in url:
            return self.github
        name = url.rsplit("/", 1)[-1]
        if name in self.vault_status:
            return Resp(self.vault_status[name], {"error": {"code": "Forbidden"}})
        return Resp(200, {"value": self.secrets[name]}) if name in self.secrets else Resp(404, {})

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if self.boom:
            raise self.boom
        return self.entra


def load(env=None, **kw):
    s = FakeSession(**kw)
    return ss.load_credentials({**VAULT_ENV, **(env or {})}, s), s


def test_vault_happy_path():
    (creds, source), s = load()
    assert source == "azure-key-vault"
    assert creds == {"ISC_TENANT": "acme", "ISC_CLIENT_ID": "isc-id-9999",
                     "ISC_CLIENT_SECRET": "isc-secret-8888", "ISC_BASE_URL": "https://acme.api.example.com"}


def test_oidc_and_entra_requests_are_correct():
    _, s = load()
    gh, entra, *vault = s.calls
    assert gh[2]["params"] == {"audience": "api://AzureADTokenExchange"}
    assert gh[2]["headers"]["Authorization"] == "Bearer runner-bearer"
    assert entra[1] == f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    assert entra[2]["data"] == {
        "grant_type": "client_credentials", "client_id": CLIENT, "scope": "https://vault.azure.net/.default",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": GH_JWT}  # no client_secret anywhere
    assert [c[1] for c in vault] == [f"https://kv-isc-sandbox.vault.azure.net/secrets/{n}" for n in ss.SECRETS.values()]
    assert all(c[2]["headers"] == {"Authorization": f"Bearer {KV_TOKEN}"} for c in vault)
    assert all(c[2]["params"] == {"api-version": "7.4"} for c in vault)


def test_vault_wins_over_stale_env():
    (creds, _), _ = load({"ISC_TENANT": "stale", "ISC_CLIENT_SECRET": "stale", "ISC_CLIENT_ID": "stale"})
    assert creds["ISC_TENANT"] == "acme" and creds["ISC_CLIENT_SECRET"] == "isc-secret-8888"


def test_optional_base_url_may_be_absent():
    (creds, _), _ = load(secrets={k: v for k, v in VALUES.items() if k != "isc-base-url"})
    assert "ISC_BASE_URL" not in creds


def test_required_secret_missing_lists_names_only():
    with pytest.raises(ss.SecretsError, match="isc-client-secret") as e:
        load(secrets={"isc-tenant": "acme", "isc-client-id": "isc-id-9999"})
    assert "isc-id-9999" not in str(e.value)


@pytest.mark.parametrize("name", ["https://evil.example/x", "evil.com/a", "a", "x" * 25, "-bad", "bad-", "a b"])
def test_vault_name_must_be_a_bare_name(name):
    s = FakeSession()
    with pytest.raises(ss.SecretsError, match="vault name"):
        ss.load_credentials({**VAULT_ENV, "AZURE_KEY_VAULT_NAME": name}, s)
    assert s.calls == []  # no token was ever sent anywhere


@pytest.mark.parametrize("drop", ["ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
def test_missing_oidc_env_explains_the_fix(drop):
    env = {k: v for k, v in VAULT_ENV.items() if k != drop}
    with pytest.raises(ss.SecretsError, match="id-token: write"):
        ss.load_credentials(env, FakeSession())


@pytest.mark.parametrize("drop", ["AZURE_TENANT_ID", "AZURE_CLIENT_ID"])
def test_missing_azure_ids(drop):
    env = {k: v for k, v in VAULT_ENV.items() if k != drop}
    with pytest.raises(ss.SecretsError, match=drop):
        ss.load_credentials(env, FakeSession())


@pytest.mark.parametrize("key", ["AZURE_TENANT_ID", "AZURE_CLIENT_ID"])
def test_azure_ids_must_be_guids(key):
    with pytest.raises(ss.SecretsError, match="GUID"):
        load({key: "not-a-guid"})


def test_github_oidc_failure():
    with pytest.raises(ss.SecretsError, match="HTTP 403"):
        load(github=Resp(403, {}))
    with pytest.raises(ss.SecretsError, match="OIDC token request failed"):
        load(github=Resp(200, {}))


def test_entra_rejection_shows_code_but_never_the_assertion():
    entra = Resp(400, {"error": "invalid_client",
                       "error_description": "AADSTS70025: no federated identity credential matches"})
    with pytest.raises(ss.SecretsError) as e:
        load(entra=entra)
    msg = str(e.value)
    assert "AADSTS70025" in msg and GH_JWT not in msg and "runner-bearer" not in msg


def test_entra_response_without_token():
    with pytest.raises(ss.SecretsError, match="Entra token exchange failed"):
        load(entra=Resp(200, {"token_type": "Bearer"}))


@pytest.mark.parametrize("status", [401, 403])
def test_vault_access_denied_names_the_role(status):
    with pytest.raises(ss.SecretsError, match="Key Vault Secrets User"):
        load(vault_status={"isc-tenant": status})


def test_vault_server_error():
    with pytest.raises(ss.SecretsError, match="HTTP 500"):
        load(vault_status={"isc-tenant": 500})


@pytest.mark.parametrize("exc", [requests.ConnectionError("dns https://x?token=SECRET"), requests.Timeout("slow")])
def test_network_errors_become_secrets_errors_without_details(exc):
    with pytest.raises(ss.SecretsError) as e:
        load(boom=exc)
    assert "SECRET" not in str(e.value) and "token=" not in str(e.value)


def test_env_fallback():
    env = {"ISC_TENANT": " acme ", "ISC_CLIENT_ID": "id", "ISC_CLIENT_SECRET": "sec", "ISC_BASE_URL": ""}
    creds, source = ss.load_credentials(env, FakeSession())
    assert source == "env"
    assert creds == {"ISC_TENANT": "acme", "ISC_CLIENT_ID": "id", "ISC_CLIENT_SECRET": "sec"}


def test_env_fallback_missing_names_what_to_set():
    with pytest.raises(ss.SecretsError, match="ISC_CLIENT_ID, ISC_CLIENT_SECRET.*AZURE_KEY_VAULT_NAME"):
        ss.load_credentials({"ISC_TENANT": "acme"}, FakeSession())


def test_blank_vault_name_falls_back_to_env():
    _, source = ss.load_credentials({"AZURE_KEY_VAULT_NAME": "  ", "ISC_TENANT": "t", "ISC_CLIENT_ID": "i",
                                     "ISC_CLIENT_SECRET": "s"}, FakeSession())
    assert source == "env"


def test_add_mask_only_inside_github_actions(monkeypatch, capsys):
    creds = {"ISC_TENANT": "acme", "ISC_CLIENT_ID": "id-1", "ISC_CLIENT_SECRET": "line1\nline2"}
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    ss.mask_in_github_logs(creds)
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    ss.mask_in_github_logs(creds)
    assert capsys.readouterr().out.splitlines() == ["::add-mask::id-1", "::add-mask::line1", "::add-mask::line2"]


# ---- end to end through main(): vault credentials reach the client and never reach the run log ----

def test_main_loads_from_vault_and_keeps_secrets_out_of_the_log(tmp_path, monkeypatch, defaults, csv_file, isc):
    seen = {}
    monkeypatch.setattr(onboard, "ISCClient", lambda *a, **k: (seen.update(args=a, kw=k), isc)[1])
    monkeypatch.setattr(onboard, "load_credentials", lambda: ({"ISC_TENANT": "acme", "ISC_CLIENT_ID": "isc-id-9999",
                                                              "ISC_CLIENT_SECRET": "isc-secret-8888"}, "azure-key-vault"))
    monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "kv-isc-sandbox")
    app = {"apps": [{"appName": "Vault-App", "connectorType": "delimited", "owner": "asmith",
                     "application": "Vault App",
                     "delimited": {"accountFile": csv_file(), "schema": {
                         "identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                         "entitlementColumns": ["Role"]},
                         "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]},
                     "accessProfiles": [{"name": "Vault AP", "entitlementValues": ["Approver"]}]}]}
    (tmp_path / "defaults.yaml").write_text(yaml.safe_dump(defaults))
    (tmp_path / "app_a.yaml").write_text(yaml.safe_dump(app))
    monkeypatch.setattr("sys.argv", ["onboard.py", "--apps", str(tmp_path / "app_*.yaml"), "--defaults",
                                     str(tmp_path / "defaults.yaml"), "--report", str(tmp_path / "r.json")])
    with pytest.raises(SystemExit) as e:
        onboard.main()
    assert e.value.code == 0
    assert seen["args"][:3] == ("acme", "isc-id-9999", "isc-secret-8888")
    assert seen["kw"]["base_url"] is None

    assert redact("secret=isc-secret-8888 id isc-id-9999") == "secret=[REDACTED] id [REDACTED]"
    events = [json.loads(line) for f in _logs() for line in f.read_text().splitlines()]
    loaded = [x for x in events if x["event"] == "secrets_loaded"]
    assert loaded and loaded[-1]["source"] == "azure-key-vault" and loaded[-1]["vault"] == "kv-isc-sandbox"
    assert "isc-secret-8888" not in json.dumps(events)


def _logs():
    return sorted(Path(os.environ["ISC_LOG_DIR"]).rglob("*.jsonl"))


def test_main_fails_cleanly_and_logs_when_the_vault_is_unreachable(tmp_path, monkeypatch, defaults, csv_file):

    def boom():
        raise ss.SecretsError("vault 'kv-isc-sandbox' refused access to 'isc-tenant' (HTTP 403)")
    monkeypatch.setattr(onboard, "load_credentials", boom)
    app = {"apps": [{"appName": "X-App", "connectorType": "delimited", "owner": "asmith", "application": "X App",
                     "delimited": {"accountFile": csv_file(), "schema": {
                         "identityAttribute": "EmployeeID", "displayAttribute": "UserName",
                         "entitlementColumns": ["Role"]},
                         "correlation": [{"identityAttr": "uid", "accountAttr": "UserName"}]}}]}
    (tmp_path / "defaults.yaml").write_text(yaml.safe_dump(defaults))
    (tmp_path / "app_a.yaml").write_text(yaml.safe_dump(app))
    monkeypatch.setattr("sys.argv", ["onboard.py", "--apps", str(tmp_path / "app_*.yaml"), "--defaults",
                                     str(tmp_path / "defaults.yaml"), "--report", str(tmp_path / "r.json")])
    with pytest.raises(ss.SecretsError):
        onboard.main()
    events = [json.loads(line) for f in _logs() for line in f.read_text().splitlines()]
    assert [x["event"] for x in events][-2:] == ["run_crashed", "run_end"]
    assert "HTTP 403" in events[-2]["message"]
