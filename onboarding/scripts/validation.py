"""Offline checks (no API calls) shared by several connectors."""
import csv
import os
import re

from common import norm, read_csv

MAX_NAME = 128  # assumed ISC limit (same as the governance group default); verify per tenant
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def name_problem(v):
    if not isinstance(v, str) or not v.strip():
        return "must be a non-empty string"
    if v != v.strip() or CTRL_RE.search(v):
        return "must not have leading/trailing whitespace or control characters"
    if len(v) > MAX_NAME:
        return f"must be at most {MAX_NAME} characters"


def check_text(errs, obj, key, label=None, required=True):
    v = obj.get(key)
    if v is None or (required and v == ""):
        if required:
            errs.append(f"{label or key} is required")
    elif problem := name_problem(v):
        errs.append(f"{label or key} {problem}")


def read_csv_checked(path, delimiter, what):
    """Returns (columns, rows, errors) without ever raising on bad files."""
    if not isinstance(path, str) or not os.path.isfile(path):
        return [], [], [f"{what} not found: {path}"]
    try:
        cols, rows = read_csv(path, delimiter)
    except (OSError, UnicodeDecodeError, csv.Error, TypeError) as e:
        return [], [], [f"{what} unreadable: {e}"]
    if not cols:
        return [], [], [f"{what} has no header row: {path}"]
    return cols, rows, []


def access_profile_problems(aps):
    if aps is None:
        return []
    if not isinstance(aps, list):
        return ["accessProfiles must be a list"]
    errs, seen = [], set()
    for i, ap in enumerate(aps):
        if not isinstance(ap, dict):
            errs.append(f"accessProfiles[{i}] must be a mapping")
            continue
        if problem := name_problem(ap.get("name")):
            errs.append(f"accessProfiles[{i}].name {problem}")
        elif norm(ap["name"]) in seen:
            errs.append(f"accessProfiles[{i}] duplicate name: {ap['name']}")
        else:
            seen.add(norm(ap["name"]))
        vals = ap.get("entitlementValues")
        if not (isinstance(vals, list) and vals and all(isinstance(v, str) and v.strip() for v in vals)):
            errs.append(f"accessProfiles[{i}].entitlementValues must be a non-empty list of strings")
    return errs


def source_app_problems(app):
    """Checks common to connectors that create a source and then an application with access profiles."""
    errs = []
    check_text(errs, app, "sourceName", required=False)
    check_text(errs, app, "application", required=False)
    return errs + access_profile_problems(app.get("accessProfiles"))


def duplicates(label, names):
    groups = {}
    for n in names:
        if isinstance(n, str) and n.strip():
            groups.setdefault(norm(n), []).append(n)
    return [f"duplicate {label}: {v[0]}" for v in groups.values() if len(v) > 1]
