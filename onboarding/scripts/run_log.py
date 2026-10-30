"""Structured JSON-lines run log for the onboarding pipeline (stdlib only).

One file per run: onboarding/logs/<UTC date>/<HHMMSS>Z-<run id>.jsonl, one event per line.
Every line passes through redact() before it is written. Logging never breaks a run:
the first write/open error is warned about on the console and the log switches itself off.
"""
import json
import logging
import os
import re
import secrets
import subprocess
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("runlog")

BODY_MAX = 300      # HTTP response bodies
MESSAGE_MAX = 1000
TRACEBACK_MAX = 4000
SECRET_ENV = ("ISC_CLIENT_ID", "ISC_CLIENT_SECRET")
MASK = "[REDACTED]"

_known_secrets = set()

_SECRET_KEY = r"[\w-]*(?:token|secret|password|passwd|api[_-]?key|authorization|client[_-]?id)"
_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {MASK}"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*"), MASK),  # JWT
    # key: value / key=value / "key": "value", also inside JSON-escaped strings
    (re.compile(rf"""(?i)({_SECRET_KEY}\b\\?["']?\s*[:=]\s*\\?["']?)(?!\[REDACTED)[^"'\s,&}}\\]+"""),
     rf"\1{MASK}"),
    # long opaque strings; pure hex (git shas, ISC ids) is deliberately left alone
    (re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[G-Zg-z])[\w-]{40,}\b"), MASK),
    (re.compile(r"\b([\w.%+-])[\w.%+-]*@([\w-]+(?:\.[\w-]+)+)"), r"\1***@\2"),  # e-mail addresses
]


def register_secret(value):
    """Mask this exact value wherever it appears (in addition to the pattern rules)."""
    if value and len(value) >= 4:
        _known_secrets.add(value)


def redact(text):
    text = str(text)
    for s in _known_secrets:
        # also the JSON-escaped spelling, since whole serialized lines are redacted
        text = text.replace(s, MASK).replace(json.dumps(s)[1:-1], MASK)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _git_sha():
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5, check=True)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class RunLog:
    """Append-only event log for one run. RunLog(root=None) is a disabled no-op instance."""

    def __init__(self, root="onboarding/logs", *, target=None, dry_run=False):
        for name in SECRET_ENV:
            register_secret(os.environ.get(name))
        self.run_id = re.sub(r"[^\w.-]", "_", os.environ.get("GITHUB_RUN_ID") or secrets.token_hex(4))
        self.dry_run = dry_run
        self.app = None  # current app, stamped on events that do not name one
        self.path = None
        self._fh = None
        if root:
            self._open(Path(root))
        self.event("run_start", message="run started", attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
                   actor=os.environ.get("GITHUB_ACTOR"), target=target, sha=_git_sha())

    def _open(self, root):
        now = datetime.now(timezone.utc)
        path = root / f"{now:%Y-%m-%d}" / f"{now:%H%M%S}Z-{self.run_id}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", buffering=1, encoding="utf-8")  # line-buffered
            self.path = path
        except OSError as e:
            log.warning("run log disabled, cannot open %s: %s", path, e)

    def event(self, event, level="INFO", *, message=None, step=None, app=None, **fields):
        if not self._fh:
            return
        try:
            rec = {"ts": _now(), "level": level, "event": event, "run": self.run_id,
                   "app": app or self.app, "step": step,
                   "message": redact(message)[:MESSAGE_MAX] if message else None, **fields}
            if "body" in fields:  # redact first so truncation cannot split a secret
                rec["body"] = redact(fields["body"])[:BODY_MAX]
            if self.dry_run:
                rec["dry_run"] = True
            line = json.dumps({k: v for k, v in rec.items() if v is not None},
                              default=str, ensure_ascii=False)
            self._fh.write(redact(line) + "\n")
        except Exception as e:  # logging must never break a run
            log.warning("run log disabled after error: %s", e)
            self.close()

    def failure(self, event, exc, **fields):
        tb = "".join(traceback.format_exception(exc))[-TRACEBACK_MAX:]
        self.event(event, "ERROR", message=str(exc), error=type(exc).__name__, traceback=tb, **fields)

    def end(self, results, crashed=False):
        counts = Counter(r["status"] for r in results)
        self.app = None
        ok = not crashed and set(counts) <= {"ok"}
        self.event("run_end", "INFO" if ok else "ERROR", crashed=crashed,
                   message=f"{len(results)} app(s): " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())),
                   counts=dict(counts), apps={r["app"]: r["status"] for r in results})
        self.close()

    def close(self):
        fh, self._fh = self._fh, None
        if fh:
            try:
                fh.close()
            except OSError:
                pass
