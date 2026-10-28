"""Thin SailPoint ISC REST client: OAuth client-credentials, retry/backoff on 429/5xx,
paging, and a global dry-run switch that blocks every write call."""
import json
import logging
import time

import requests

log = logging.getLogger("isc")


class ISCError(Exception):
    pass


class ISCClient:
    def __init__(self, tenant, client_id, client_secret, dry_run=False, base_url=None):
        self.base = (base_url or f"https://{tenant}.api.identitynow.com").rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.dry_run = dry_run
        self.session = requests.Session()
        self._token = None
        self._exp = 0

    # ---------- auth ----------
    def _auth(self, force=False):
        if not force and self._token and time.time() < self._exp - 60:
            return
        r = requests.post(
            f"{self.base}/oauth/token",
            data={"grant_type": "client_credentials",
                  "client_id": self.client_id,
                  "client_secret": self.client_secret},
            timeout=30,
        )
        if not r.ok:
            raise ISCError(f"Token request failed: {r.status_code} {r.text[:300]}")
        d = r.json()
        self._token = d["access_token"]
        self._exp = time.time() + int(d.get("expires_in", 700))
        self.session.headers["Authorization"] = f"Bearer {self._token}"

    # ---------- core ----------
    def request(self, method, path, *, params=None, json_body=None, data=None,
                files=None, headers=None, write=False, retries=6):
        if write and self.dry_run:
            preview = json.dumps(json_body)[:600] if json_body is not None else ""
            log.info("[DRY-RUN] %s %s %s", method, path, preview)
            return None

        self._auth()
        h = {"X-SailPoint-Experimental": "true"}
        h.update(headers or {})
        for attempt in range(retries):
            if files:  # rewind file handles on retry
                for f in files.values():
                    if hasattr(f[1], "seek"):
                        f[1].seek(0)
            r = self.session.request(method, self.base + path, params=params, json=json_body,
                                     data=data, files=files, headers=h, timeout=180)
            if r.status_code == 401 and attempt == 0:
                self._auth(force=True)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                try:
                    wait = int(r.headers.get("Retry-After", ""))
                except ValueError:
                    wait = min(2 ** attempt, 60)
                log.warning("%s %s -> %s, retrying in %ss", method, path, r.status_code, wait)
                time.sleep(wait)
                continue
            if not r.ok:
                raise ISCError(f"{method} {path} -> {r.status_code}: {r.text[:1000]}")
            if r.content and "json" in r.headers.get("Content-Type", ""):
                return r.json()
            return None
        raise ISCError(f"{method} {path} failed after {retries} attempts")

    def patch(self, path, ops, write=True):
        """JSON-Patch call (application/json-patch+json)."""
        if write and self.dry_run:
            log.info("[DRY-RUN] PATCH %s %s", path, json.dumps(ops)[:600])
            return None
        return self.request("PATCH", path, data=json.dumps(ops),
                            headers={"Content-Type": "application/json-patch+json"})

    def get_all(self, path, params=None, page_size=250):
        params = dict(params or {})
        out, offset = [], 0
        while True:
            params.update({"limit": page_size, "offset": offset})
            page = self.request("GET", path, params=params) or []
            out.extend(page)
            if len(page) < page_size:
                return out
            offset += page_size
