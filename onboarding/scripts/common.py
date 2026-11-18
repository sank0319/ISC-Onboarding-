"""Small helpers shared by every module: YAML/CSV loading, dict merging, name normalising."""
import copy
import csv
from pathlib import Path

import yaml


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_defaults(path):
    """Global defaults.yaml plus every onboarding/connectors/<type>/template.yaml next to it (the connector's own
    settings and templates). Values in defaults.yaml win, so an older all-in-one defaults.yaml keeps working."""
    merged = {}
    for tpl in sorted((Path(path).resolve().parent.parent / "connectors").glob("*/*-template.yaml")):
        merged = deep_merge(merged, load_yaml(tpl))
    return deep_merge(merged, load_yaml(path))


def deep_merge(base, override):
    out = copy.deepcopy(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def esc(v):
    """Escape a value for an ISC filter string literal (backslash first, then quote)."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def norm(name):
    return " ".join(name.split()).casefold()


def text_or(value, default):
    return value if isinstance(value, str) and value.strip() else default


def read_csv(path, delimiter=","):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        return reader.fieldnames or [], list(reader)
