"""Load the ISC API credentials from HashiCorp Vault (stdlib + requests only, no vendor SDK).

Flow (inside GitHub Actions, no stored credential):
  1. GitHub OIDC token   GET  $ACTIONS_ID_TOKEN_REQUEST_URL?audience=<AUDIENCE>
  2. Vault login         POST {VAULT_ADDR}/v1/auth/<VAULT_AUTH_MOUNT>/login   {"role", "jwt"}
  3. KV v2 read          GET  {VAULT_ADDR}/v1/<VAULT_KV_MOUNT>/data/<VAULT_SECRET_PATH>
  4. Revoke the token    POST {VAULT_ADDR}/v1/auth/token/revoke-self          (best effort)

Backend choice: VAULT_ADDR set -> Vault (ISC_* env vars are then ignored on purpose, so a stale
value cannot override Vault). Otherwise the ISC_* env vars are used (local runs / tests).

Environment variables
  VAULT_ADDR         https://host:8200 (https required; plain http only for localhost/127.0.0.1)
  VAULT_ROLE         JWT-auth role name (required in Vault mode)
  VAULT_SECRET_PATH  e.g. isc/sandbox (required in Vault mode); one path per GitHub environment
  VAULT_NAMESPACE    optional, sent as X-Vault-Namespace (HCP Vault Dedicated: "admin")
  VAULT_AUTH_MOUNT   default "jwt"
  VAULT_KV_MOUNT     default "secret"
  VAULT_AUDIENCE     default DEFAULT_AUDIENCE; must equal the role's bound_audiences

Secret keys: ISC_TENANT, ISC_CLIENT_ID, ISC_CLIENT_SECRET (required), ISC_BASE_URL (optional).

`python secrets_store.py --claims` prints iss/sub/aud of the GitHub OIDC token (not secret) so the
exact `sub` can be copied into the Vault role's bound_claims.

Nothing secret (values, Vault tokens, the OIDC JWT) is ever put in an exception message or log line.
"""
import argparse
import base64
import json
import os
import re
import sys
from urllib.parse import urlsplit

import requests

# Audience we ask GitHub to put in the OIDC token. Any string works as long as the Vault role's
# `bound_audiences` contains exactly the same value. Override per run with VAULT_AUDIENCE.
DEFAULT_AUDIENCE = "vault-isc-onboarding"

# Vault API prefix and revoke endpoint (verified in the HashiCorp API docs; see README).
API_PREFIX = "/v1"
REVOKE_SELF_PATH = "/v1/auth/token/revoke-self"
NAMESPACE_HEADER = "X-Vault-Namespace"

REQUIRED_KEYS = ("ISC_TENANT", "ISC_CLIENT_ID", "ISC_CLIENT_SECRET")
OPTIONAL_KEYS = ("ISC_BASE_URL",)
SOURCE_VAULT, SOURCE_ENV = "hashicorp-vault", "env"
TIMEOUT = (5, 20)  # (connect, read) seconds, applied to every request
LOCAL_HOSTS = ("localhost", "127.0.0.1")
ERR_MAX = 200

_SEG = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
_RE_MOUNT = re.compile(rf"^{_SEG}(?:/{_SEG})*$")
_RE_PATH = re.compile(rf"^{_SEG}(?:/{_SEG})*$")
_RE_ROLE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_RE_NAMESPACE = re.compile(rf"^{_SEG}(?:/{_SEG})*$")
_RE_AUDIENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:/._-]{0,199}$")
_RE_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
_RE_VAULT_TOKEN = re.compile(r"\b(?:hv[sbr]\.|s\.)[A-Za-z0-9_-]{8,}")


class SecretsError(Exception):
    """Credentials could not be obtained. The message never contains secret material."""


def _setting(env, name, default=None):
    v = (env.get(name) or "").strip()
    return v or default


def _check(name, value, regex):
    if not regex.match(value):
        raise SecretsError(f"{name} has an invalid format (see onboarding/scripts/secrets_store.py for allowed characters)")
    return value


def validate_addr(addr):
    """Return VAULT_ADDR without a trailing slash; https only (http for localhost dev)."""
    try:
        u = urlsplit(addr)
        host, _ = u.hostname, u.port  # .port raises ValueError for junk ports
    except ValueError:
        raise SecretsError("VAULT_ADDR is not a valid URL")
    if u.scheme == "https":
        pass
    elif u.scheme == "http" and host in LOCAL_HOSTS:
        pass
    else:
        raise SecretsError("VAULT_ADDR must use https:// (plain http is allowed only for localhost/127.0.0.1)")
    if not host or u.username is not None or u.password is not None or u.query or u.fragment \
            or u.path not in ("", "/") or any(c.isspace() for c in addr):
        raise SecretsError("VAULT_ADDR must look like https://host[:port] (no credentials, path, query or fragment)")
    return f"{u.scheme}://{u.netloc}"


class _Scrub:
    """Collects values that must never be shown, and cleans free text from remote servers."""

    def __init__(self):
        self.values = set()

    def add(self, v):
        if v:
            self.values.add(v)

    def clean(self, text):
        text = str(text)
        for v in self.values:
            text = text.replace(v, "[REDACTED]")
        text = _RE_VAULT_TOKEN.sub("[REDACTED]", _RE_JWT.sub("[REDACTED]", text))
        text = "".join(ch if ch.isprintable() else " " for ch in text)
        return text[:ERR_MAX]


def _mask(env, *values):
    """Ask the GitHub runner to mask values in all later log output."""
    if env.get("GITHUB_ACTIONS") != "true":
        return
    for v in values:
        for line in str(v or "").splitlines():
            if line.strip():
                print(f"::add-mask::{line}", flush=True)


def _send(call, stage):
    problem = None
    try:
        return call()
    except requests.exceptions.Timeout:
        problem = "request timed out"
    except requests.exceptions.RequestException as e:
        problem = f"network error ({type(e).__name__})"  # class name only: str(e) can hold the URL
    # raised outside the except block so no exception context (and its message) is attached
    raise SecretsError(f"{stage}: {problem}")


def _vault_errors(r, scrub):
    try:
        errs = r.json().get("errors")
    except (ValueError, AttributeError):
        return ""
    if isinstance(errs, list) and errs:
        return scrub.clean("; ".join(str(e) for e in errs))
    return ""


def _json(r, stage):
    try:
        d = r.json()
    except ValueError:
        d = None
        bad = True
    else:
        bad = False
    if bad:
        raise SecretsError(f"{stage}: response is not valid JSON")
    if not isinstance(d, dict):
        raise SecretsError(f"{stage}: unexpected response shape")
    return d


def _expect_ok(r, stage, scrub, hint=""):
    if 200 <= r.status_code < 300:
        return
    detail = _vault_errors(r, scrub)
    msg = f"{stage}: HTTP {r.status_code}"
    if detail:
        msg += f" ({detail})"
    if hint:
        msg += f" - {hint}"
    raise SecretsError(msg)


def get_oidc_token(env, session, audience, scrub):
    url, req_token = _setting(env, "ACTIONS_ID_TOKEN_REQUEST_URL"), _setting(env, "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not req_token:
        raise SecretsError("GitHub OIDC is not available: the job needs `permissions: id-token: write` "
                           "(ACTIONS_ID_TOKEN_REQUEST_URL / _TOKEN are not set)")
    if not url.startswith("https://"):
        raise SecretsError("ACTIONS_ID_TOKEN_REQUEST_URL must be an https URL")
    scrub.add(req_token)
    _mask(env, req_token)
    stage = "GitHub OIDC token request"
    r = _send(lambda: session.get(url, params={"audience": audience},
                                  headers={"Authorization": f"Bearer {req_token}"},
                                  timeout=TIMEOUT, allow_redirects=False), stage)
    if not 200 <= r.status_code < 300:
        raise SecretsError(f"{stage}: HTTP {r.status_code}")
    jwt = _json(r, stage).get("value")
    if not isinstance(jwt, str) or not jwt:
        raise SecretsError(f"{stage}: response has no token")
    scrub.add(jwt)
    _mask(env, jwt)
    return jwt


def _revoke(session, addr, headers):
    try:  # best effort: the token is short-lived anyway
        session.post(addr + REVOKE_SELF_PATH, headers=headers, timeout=TIMEOUT, allow_redirects=False)
    except Exception:  # noqa: BLE001 - must never fail the run
        pass


def _vault_settings(env):
    addr = validate_addr(_setting(env, "VAULT_ADDR"))
    missing = [n for n in ("VAULT_ROLE", "VAULT_SECRET_PATH") if not _setting(env, n)]
    if missing:
        raise SecretsError("VAULT_ADDR is set, so Vault is used; also set " + " and ".join(missing))
    ns = _setting(env, "VAULT_NAMESPACE")
    return {
        "addr": addr,
        "role": _check("VAULT_ROLE", _setting(env, "VAULT_ROLE"), _RE_ROLE),
        "path": _check("VAULT_SECRET_PATH", _setting(env, "VAULT_SECRET_PATH"), _RE_PATH),
        "auth_mount": _check("VAULT_AUTH_MOUNT", _setting(env, "VAULT_AUTH_MOUNT", "jwt"), _RE_MOUNT),
        "kv_mount": _check("VAULT_KV_MOUNT", _setting(env, "VAULT_KV_MOUNT", "secret"), _RE_MOUNT),
        "namespace": _check("VAULT_NAMESPACE", ns, _RE_NAMESPACE) if ns else None,
        "audience": _check("VAULT_AUDIENCE", _setting(env, "VAULT_AUDIENCE", DEFAULT_AUDIENCE), _RE_AUDIENCE),
    }


def _read_vault(env, session):
    cfg = _vault_settings(env)
    scrub = _Scrub()
    jwt = get_oidc_token(env, session, cfg["audience"], scrub)
    ns_hdr = {NAMESPACE_HEADER: cfg["namespace"]} if cfg["namespace"] else {}
    base = cfg["addr"] + API_PREFIX

    stage = "Vault login"
    r = _send(lambda: session.post(f"{base}/auth/{cfg['auth_mount']}/login",
                                   json={"role": cfg["role"], "jwt": jwt}, headers=dict(ns_hdr),
                                   timeout=TIMEOUT, allow_redirects=False), stage)
    _expect_ok(r, stage, scrub, "check the role name and its bound_audiences/bound_claims "
                                "(run `secrets_store.py --claims` to see the token's sub/aud)")
    auth = _json(r, stage).get("auth")
    token = auth.get("client_token") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not token:
        raise SecretsError(f"{stage}: response has no client token")
    scrub.add(token)
    _mask(env, token)
    headers = {"X-Vault-Token": token, **ns_hdr}

    try:
        stage = "Vault secret read"
        r = _send(lambda: session.get(f"{base}/{cfg['kv_mount']}/data/{cfg['path']}", headers=dict(headers),
                                      timeout=TIMEOUT, allow_redirects=False), stage)
        hint = {403: "the role's policy needs `read` on the KV v2 data path",
                404: "secret not found (check VAULT_KV_MOUNT is a KV v2 mount and VAULT_SECRET_PATH/VAULT_NAMESPACE)"}
        _expect_ok(r, stage, scrub, hint.get(r.status_code, ""))
        outer = _json(r, stage).get("data")
        data = outer.get("data") if isinstance(outer, dict) else None
        if not isinstance(data, dict):
            raise SecretsError(f"{stage}: response has no data.data (is the mount KV v2?)")
        return cfg, data
    finally:
        _revoke(session, cfg["addr"], headers)


def _pick(data, where):
    """Pull the credential keys out of a mapping; error names only the keys, never values."""
    creds, bad = {}, []
    for k in REQUIRED_KEYS + OPTIONAL_KEYS:
        v = data.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            if k in REQUIRED_KEYS:
                bad.append(k)
            continue
        if not isinstance(v, str):
            bad.append(k)
            continue
        creds[k] = v.strip()
    if bad:
        raise SecretsError(f"{where} is missing or has empty/non-string value(s) for: {', '.join(bad)}")
    return creds


def load_credentials(env=None, session=None):
    """Return (creds, source): creds has ISC_TENANT, ISC_CLIENT_ID, ISC_CLIENT_SECRET[, ISC_BASE_URL]."""
    env = os.environ if env is None else env
    if _setting(env, "VAULT_ADDR"):
        cfg, data = _read_vault(env, session or requests.Session())
        creds = _pick(data, f"Vault secret {cfg['kv_mount']}/{cfg['path']}")
        source = SOURCE_VAULT
    else:
        missing = [k for k in REQUIRED_KEYS if not _setting(env, k)]
        if missing:
            raise SecretsError("No credentials found. Set VAULT_ADDR (+ VAULT_ROLE, VAULT_SECRET_PATH) to use "
                               "HashiCorp Vault, or set " + ", ".join(missing) + " for a local run.")
        creds = _pick({k: env.get(k) for k in REQUIRED_KEYS + OPTIONAL_KEYS}, "environment")
        source = SOURCE_ENV
    _mask(env, creds["ISC_CLIENT_ID"], creds["ISC_CLIENT_SECRET"])
    return creds, source


def oidc_claims(env=None, session=None):
    """The non-secret identity claims (iss/sub/aud) of this job's GitHub OIDC token."""
    env = os.environ if env is None else env
    audience = _check("VAULT_AUDIENCE", _setting(env, "VAULT_AUDIENCE", DEFAULT_AUDIENCE), _RE_AUDIENCE)
    jwt = get_oidc_token(env, session or requests.Session(), audience, _Scrub())
    try:
        part = jwt.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        raise SecretsError("GitHub OIDC token is not a decodable JWT") from None
    if not isinstance(payload, dict):
        raise SecretsError("GitHub OIDC token payload is not an object")
    return {k: payload.get(k) for k in ("iss", "sub", "aud")}


def main(argv=None, env=None, session=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--claims", action="store_true",
                   help="print iss/sub/aud of the GitHub OIDC token (for the Vault role's bound_claims)")
    args = p.parse_args(argv)
    if not args.claims:
        p.error("nothing to do; use --claims (credentials are loaded by onboard.py / connectivity_check.py)")
    try:
        claims = oidc_claims(env, session)
    except SecretsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for k, v in claims.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
