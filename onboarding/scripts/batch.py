"""A batch of app configs: loading (waves, environment), selection by apps CSV, and offline validation."""
import glob
import re
import sys
from pathlib import Path

import yaml

import connectors
from common import load_yaml, norm
from validation import check_text, duplicates, read_csv_checked

APPS_CSV_COLUMNS = ("applicationName", "applicationType")
WAVE_DIR_RE = re.compile(r"^wave-(\d+)$")


# ---------------------------------------------------------------- loading
def file_apps(path):
    """A file can hold one app or a bulk 'apps:' list."""
    try:
        cfg = load_yaml(path)
    except yaml.YAMLError as e:
        sys.exit(f"{path}: invalid YAML: {e}")
    if not isinstance(cfg, dict):
        sys.exit(f"{path}: expected a mapping, got {type(cfg).__name__}")
    if "apps" not in cfg:
        return [cfg]
    if not isinstance(cfg["apps"], list):
        sys.exit(f"{path}: 'apps' must be a list")
    return cfg["apps"]


def wave_of(path):
    """Wave number from a 'wave-<n>' parent folder, else None (connectors without waves)."""
    for part in reversed(Path(path).parts[:-1]):
        if m := WAVE_DIR_RE.match(part):
            return int(m[1])


def load_apps(pattern, wave=None):
    """[(file, app)] for every file matching the glob (recursive), optionally only one wave."""
    files = sorted(glob.glob(pattern, recursive=True))
    if wave is not None:
        files = [f for f in files if wave_of(f) == wave]
    entries = [(f, a) for f in files for a in file_apps(f)]
    if not entries:
        sys.exit(f"No app configs matched {pattern}" + (f" in wave {wave}" if wave is not None else ""))
    return entries


def target_env(target):
    """Feed-file environment for an ISC_TARGET: only 'production' reads production feeds."""
    return "production" if target == "production" else "sandbox"


def with_env(obj, env):
    """Replace the {env} placeholder in every string of a config."""
    if isinstance(obj, str):
        return obj.replace("{env}", env)
    if isinstance(obj, list):
        return [with_env(o, env) for o in obj]
    if isinstance(obj, dict):
        return {k: with_env(v, env) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- selection (apps CSV)
def select_apps(entries, csv_path):
    """Pick (and order) apps by an application CSV. Returns (entries, errors)."""
    cols, rows, errs = read_csv_checked(csv_path, ",", "apps CSV")
    if errs:
        return [], errs
    if missing := [c for c in APPS_CSV_COLUMNS if c not in cols]:
        return [], [f"apps CSV {csv_path} needs columns {list(APPS_CSV_COLUMNS)}, missing {missing} (has {cols})"]
    if not rows:
        return [], [f"apps CSV {csv_path} has no rows"]
    by_name = {}
    for e in entries:
        if isinstance(e[1], dict) and isinstance(e[1].get("appName"), str):
            by_name.setdefault(norm(e[1]["appName"]), []).append(e)
    picked, seen = [], {}
    for line, row in enumerate(rows, start=2):
        name, kind = ((row.get(c) or "").strip() for c in APPS_CSV_COLUMNS)
        where = f"apps CSV line {line}"
        if not name or not kind:
            errs.append(f"{where}: applicationName and applicationType are both required")
        elif kind not in connectors.REGISTRY:
            errs.append(f"{where} ({name}): unknown applicationType '{kind}' (known: {sorted(connectors.REGISTRY)})")
        elif norm(name) in seen:
            errs.append(f"{where} ({name}): duplicate of line {seen[norm(name)]}")
        elif not (found := by_name.get(norm(name))):
            errs.append(f"{where} ({name}): no app with this appName in the selected configs")
        elif len(found) > 1:
            errs.append(f"{where} ({name}): {len(found)} configs share this appName: {[f[0] for f in found]}")
        elif found[0][1].get("connectorType") != kind:
            errs.append(f"{where} ({name}): applicationType '{kind}' does not match connectorType "
                        f"'{found[0][1].get('connectorType')}' in {found[0][0]}")
        else:
            seen[norm(name)] = line
            picked.append(found[0])
    return picked, errs


# ---------------------------------------------------------------- layout (connector folders)
def layout_errors(entries):
    """Each connector folder has two parts: apps/ (the app definitions that create the applications)
    and data/ (feed files). Apps under onboarding/connectors/<type>/apps/ must be of that type and read
    their feed from data/{env}/<source name>.csv (accounts) or data/{env}/<name>-group.csv (groups)."""
    errs = []
    for path, app in entries:
        parts = Path(path).parts
        i = next((i for i in range(len(parts) - 1) if parts[i:i + 2] == ("onboarding", "connectors")), None)
        if i is None or not isinstance(app, dict):
            continue
        kind, who = app.get("connectorType"), f"{path}: [{app.get('appName')}]"
        if parts[i + 2:i + 4] != (kind, "apps") or len(parts) < i + 5:
            errs.append(f"{who} connectorType '{kind}' must live in onboarding/connectors/{kind}/apps/")
        elif kind in connectors.REGISTRY and (feed := connectors.get(kind).feed(app)):
            configured, name = feed
            if isinstance(configured, str) and Path(configured).parts[-5:] != ("connectors", kind, "data", "{env}", name):
                errs.append(f"{who} feed file must be .../connectors/{kind}/data/{{env}}/{name}, got {configured}")
    return errs


# ---------------------------------------------------------------- validation (no API calls)
def validate_app(app, d):
    if not isinstance(app, dict):
        return [f"app entry must be a mapping, got {type(app).__name__}"]
    errs, t = [], app.get("connectorType")
    name = app["appName"] if isinstance(app.get("appName"), str) and app["appName"] else "<missing appName>"
    check_text(errs, app, "appName")
    check_text(errs, app, "owner", "owner (alias or email)")
    if not isinstance(t, str) or t not in connectors.REGISTRY:
        errs.append(f"connectorType must be one of {sorted(connectors.REGISTRY)}")
    else:
        errs += connectors.get(t).validate(app, d)
    return [f"[{name}] {e}" for e in errs]


def creates_source(app):
    t = app.get("connectorType")
    return not isinstance(t, str) or getattr(connectors.REGISTRY.get(t), "creates_source", True)


def batch_errors(apps, defaults):
    """Everything wrong with a batch before any API call: per-app problems and name clashes
    (ISC names are unique case-insensitively; apps, applications and sources each have their own namespace)."""
    maps = [a for a in apps if isinstance(a, dict)]
    sources = [a.get("sourceName", a.get("appName")) for a in maps if creates_source(a)]
    errors = (duplicates("appName", [a.get("appName") for a in maps])
              + duplicates("application", [a.get("application") for a in maps])
              + duplicates("source name", sources))
    for a in apps:
        errors += validate_app(a, defaults)
    return errors
