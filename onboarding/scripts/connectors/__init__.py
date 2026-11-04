"""Connector registry. One module per connector type; modules not starting with '_' are imported
automatically, and each registers itself with @register. See _template.py."""
import importlib
import pkgutil

REGISTRY = {}


class Connector:
    """Base class: set `type`, override `validate` and `handle`, decorate with @register."""
    type = ""
    creates_source = True  # False when the source already exists in ISC (e.g. AD)

    def validate(self, app, defaults):
        """Connector-specific config problems (list of strings); no API calls."""
        return []

    def handle(self, ob, app, result):
        """Onboard one app through the Onboarder `ob`; record outcomes in `result`."""
        raise NotImplementedError

    def feed(self, app):
        """(configured feed path, expected file name) for connectors fed by a file, else None."""
        return None


class UnknownConnector(ValueError):
    pass


def register(cls):
    if not cls.type:
        raise ValueError(f"{cls.__name__} must set `type`")
    if cls.type in REGISTRY:
        raise ValueError(f"connector type '{cls.type}' is already registered by {type(REGISTRY[cls.type]).__name__}")
    REGISTRY[cls.type] = cls()
    return cls


def get(type_name):
    try:
        return REGISTRY[type_name]
    except (KeyError, TypeError):
        raise UnknownConnector(f"unknown connector type {type_name!r} (known: {sorted(REGISTRY)})") from None


def discover():
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")


discover()
