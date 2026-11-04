"""Small helpers shared by every module: YAML/CSV loading, dict merging, name normalising."""
import copy
import csv

import yaml


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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
