"""Connector registry: auto-discovery, duplicate registration, unknown types."""
import importlib
import sys
import textwrap

import pytest

import batch
import connectors
from connectors import Connector, UnknownConnector, register

DUMMY = textwrap.dedent('''
    from connectors import Connector, register

    @register
    class Dummy(Connector):
        type = "dummy"

        def handle(self, ob, app, result):
            result["note"] = "dummy ran"
''')


@pytest.fixture
def scratch_registry(monkeypatch):
    """Registry and package path restored after the test."""
    monkeypatch.setattr(connectors, "REGISTRY", dict(connectors.REGISTRY))
    monkeypatch.setattr(connectors, "__path__", list(connectors.__path__))
    for name in ("dummy", "_hidden"):
        monkeypatch.delitem(sys.modules, f"connectors.{name}", raising=False)
    return connectors.REGISTRY


def test_builtin_connectors_are_registered():
    assert sorted(connectors.REGISTRY) == ["ad", "delimited", "sqlloader"]
    assert connectors.get("ad").creates_source is False
    assert connectors.get("delimited").creates_source is True


def test_dropping_a_file_in_the_package_registers_a_connector(tmp_path, scratch_registry, ob):
    (tmp_path / "dummy.py").write_text(DUMMY)
    connectors.__path__.append(str(tmp_path))
    connectors.discover()
    assert "dummy" in connectors.REGISTRY
    assert ob.run({"appName": "D", "connectorType": "dummy", "owner": "asmith"})["note"] == "dummy ran"
    assert batch.batch_errors([{"appName": "D", "connectorType": "dummy", "owner": "asmith"}], {}) == []


def test_modules_starting_with_underscore_are_not_loaded(tmp_path, scratch_registry):
    (tmp_path / "_hidden.py").write_text(DUMMY.replace('"dummy"', '"hidden"'))
    connectors.__path__.append(str(tmp_path))
    connectors.discover()
    assert "hidden" not in connectors.REGISTRY


def test_the_template_is_not_registered():
    assert "template" not in connectors.REGISTRY
    assert importlib.util.find_spec("connectors._template")


def test_duplicate_type_is_an_error(scratch_registry):
    class Again(Connector):
        type = "delimited"

    with pytest.raises(ValueError, match="'delimited' is already registered"):
        register(Again)


def test_connector_without_type_is_an_error(scratch_registry):
    class Nameless(Connector):
        pass

    with pytest.raises(ValueError, match="must set `type`"):
        register(Nameless)


@pytest.mark.parametrize("bad", ["oracle", "", None, ["ad"]])
def test_unknown_type_is_an_error(bad):
    with pytest.raises(UnknownConnector, match="known: \\['ad', 'delimited', 'sqlloader'\\]"):
        connectors.get(bad)


@pytest.mark.parametrize("bad", ["oracle", None, ["ad"]])
def test_unknown_type_fails_validation_listing_registered_types(bad, defaults):
    errs = batch.batch_errors([{"appName": "A", "connectorType": bad, "owner": "o"}], defaults)
    assert any("connectorType must be one of ['ad', 'delimited', 'sqlloader']" in e for e in errs)
