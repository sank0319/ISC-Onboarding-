"""ISC credentials: Azure Key Vault via GitHub OIDC, with ISC_* environment variables as a fallback.

Vault mode (AZURE_KEY_VAULT_NAME is set) - nothing secret is stored in GitHub:
  1. GitHub issues a short-lived OIDC token to the running job (needs `id-token: write`).
  2. Microsoft Entra exchanges it for a Key Vault access token (federated credential, no client secret).
  3. The secrets are read from the vault. Failures raise SecretsError; values never appear in errors.
Env mode (no vault configured) is for local runs and tests only. Vault mode ignores ISC_* variables
so a stale value in the environment can never override the vault.

Only stdlib + requests; no Azure SDK, which keeps the dependency surface small.
"""
import os
import re
import sys

import requests

OIDC_AUDIENCE = "api://AzureADTokenExchange"  # Entra's standard audience for GitHub federation
ENTRA_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
VAULT_HOST = "{name}.vault.azure.net"
VAULT_SCOPE = "https://vault.azure.net/.default"
VAULT_API = "7.4"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
TIMEOUT = 30

# env-style name -> secret name in the vault (one vault per GitHub environment)
SECRETS = {"ISC_TENANT": "isc-tenant", "ISC_CLIENT_ID": "isc-client-id",
           "ISC_CLIENT_SECRET": "isc-client-secret", "ISC_BASE_URL": "isc-base-url"}
OPTIONAL = {"ISC_BASE_URL"}  # blank means https://<tenant>.api.identitynow.com
MASKED = ("ISC_CLIENT_ID", "ISC_CLIENT_SECRET")  # hidden in GitHub's log viewer

_VAULT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]$")  # Azure's 3-24 char rule
_GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


class SecretsError(Exception):
    pass


def _need(env, name, why):
    value = (env.get(name) or "").strip()
    if not value:
        raise SecretsError(f"{name} is not set ({why})")
    return value


def _post(session, url, **kw):
    try:
        return session.post(url, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise SecretsError(f"{type(e).__name__} calling {url.split('?')[0]}") from None


def github_oidc_token(env, session):
    """Short-lived JWT identifying this repo/environment/ref; Entra checks it against the federated credential."""
    url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise SecretsError("no GitHub OIDC token available: add `permissions: id-token: write` to the "
                           "job (and run inside GitHub Actions)")
    try:
        r = session.get(url, params={"audience": OIDC_AUDIENCE},
                        headers={"Authorization": f"Bearer {bearer}"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise SecretsError(f"{type(e).__name__} requesting the GitHub OIDC token") from None
    token = r.json().get("value") if r.ok and r.content else None
    if not token:
        raise SecretsError(f"GitHub OIDC token request failed: HTTP {r.status_code}")
    return token


def entra_token(env, session, assertion):
    """Exchange the GitHub token for a Key Vault access token (federated credential, no client secret)."""
    tenant = _need(env, "AZURE_TENANT_ID", "Entra directory ID")
    client = _need(env, "AZURE_CLIENT_ID", "app registration / managed identity client ID")
    for label, value in (("AZURE_TENANT_ID", tenant), ("AZURE_CLIENT_ID", client)):
        if not _GUID.match(value):
            raise SecretsError(f"{label} is not a GUID")
    r = _post(session, ENTRA_URL.format(tenant=tenant), data={
        "grant_type": "client_credentials", "client_id": client, "scope": VAULT_SCOPE,
        "client_assertion_type": ASSERTION_TYPE, "client_assertion": assertion})
    body = r.json() if r.content and "json" in r.headers.get("Content-Type", "") else {}
    if not r.ok or "access_token" not in body:
        # error codes (AADSTS...) are safe to show; the assertion and tokens are never echoed
        raise SecretsError(f"Entra token exchange failed: HTTP {r.status_code} "
                           f"{body.get('error', '')} {str(body.get('error_description', ''))[:200]}".strip())
    return body["access_token"]


def vault_secret(session, vault, name, token):
    """Value of one secret, or None if it does not exist."""
    try:
        r = session.get(f"https://{VAULT_HOST.format(name=vault)}/secrets/{name}",
                        params={"api-version": VAULT_API},
                        headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise SecretsError(f"{type(e).__name__} reading secret '{name}' from vault '{vault}'") from None
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403):
        raise SecretsError(f"vault '{vault}' refused access to '{name}' (HTTP {r.status_code}): grant the "
                           "identity the 'Key Vault Secrets User' role on this vault")
    if not r.ok:
        raise SecretsError(f"reading secret '{name}' from vault '{vault}' failed: HTTP {r.status_code}")
    return r.json().get("value")


def from_vault(env, session):
    vault = env["AZURE_KEY_VAULT_NAME"].strip()
    if not _VAULT_NAME.match(vault):  # a bare name only: the access token is never sent to another host
        raise SecretsError("AZURE_KEY_VAULT_NAME must be the vault name (3-24 letters, digits, hyphens), not a URL")
    token = entra_token(env, session, github_oidc_token(env, session))
    out, missing = {}, []
    for key, secret in SECRETS.items():
        value = vault_secret(session, vault, secret, token)
        if value:
            out[key] = value
        elif key not in OPTIONAL:
            missing.append(secret)
    if missing:
        raise SecretsError(f"vault '{vault}' has no value for: {', '.join(missing)}")
    return out


def from_env(env):
    missing = [k for k in SECRETS if k not in OPTIONAL and not (env.get(k) or "").strip()]
    if missing:
        raise SecretsError(f"set {', '.join(missing)} (or configure AZURE_KEY_VAULT_NAME)")
    return {k: env[k].strip() for k in SECRETS if (env.get(k) or "").strip()}


def mask_in_github_logs(creds):
    """Ask the Actions runner to hide the credentials if they ever reach a log line."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for key in MASKED:
        for line in filter(None, creds.get(key, "").splitlines()):
            print(f"::add-mask::{line}", flush=True)


def load_credentials(env=None, session=None):
    """Return {'ISC_TENANT', 'ISC_CLIENT_ID', 'ISC_CLIENT_SECRET'[, 'ISC_BASE_URL']} and the source used."""
    env = os.environ if env is None else env
    if (env.get("AZURE_KEY_VAULT_NAME") or "").strip():
        creds, source = from_vault(env, session or requests.Session()), "azure-key-vault"
    else:
        creds, source = from_env(env), "env"
    mask_in_github_logs(creds)
    return creds, source


if __name__ == "__main__":  # `python secrets_store.py` - check access without printing any value
    try:
        found, src = load_credentials()
    except SecretsError as e:
        sys.exit(f"secrets check failed: {e}")
    print(f"OK: {len(found)} credential(s) loaded from {src}: {', '.join(sorted(found))}")
