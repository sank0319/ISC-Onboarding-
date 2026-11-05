"""discover.py is read-only and never prints credential-looking values."""
import discover
from isc_client import ISCError

SECRET = "s3cr3t-value-123"


class StubISC:
    def __init__(self, connectors=None, sources=None, schemas=None):
        self.calls = []
        self.connectors = connectors if connectors is not None else []
        self.sources = sources or []
        self.schemas = schemas or []

    def get_all(self, path, params=None):
        self.calls.append(("GET", path))
        if path == "/v3/connectors":
            return self.connectors
        return self.sources

    def request(self, method, path, **kw):
        self.calls.append((method, path))
        assert method == "GET"
        return self.sources if path == "/v3/sources" else self.schemas


SRC = {"id": "abc", "name": "HR Loader", "connector": "sqlloader-script",
       "cluster": {"id": "cl1", "name": "VA1"},
       "connectorAttributes": {"file": "/data/hr.csv", "password": SECRET, "apiToken": SECRET,
                               "url": f"https://user:{SECRET}@host/x", "nested": {"clientSecret": SECRET}}}
SCHEMA = [{"name": "account", "identityAttribute": "USER_ID", "displayAttribute": "USER_NAME",
           "attributes": [{"name": "USER_ID"}, {"name": "ROLE", "isEntitlement": True}]}]


def test_mask_hides_credential_keys_and_url_userinfo():
    out = discover.mask(SRC["connectorAttributes"])
    assert SECRET not in str(out)
    assert out["file"] == "/data/hr.csv"


def test_connectors_filters_and_reports(capsys):
    isc = StubISC(connectors=[{"scriptName": "sqlloader-script", "name": "SQL Loader", "type": "SQL Loader"},
                              {"scriptName": "delimited", "name": "Delimited File", "type": "Delimited"}])
    assert discover.main(["connectors"], isc=isc) == 0
    out = capsys.readouterr().out
    assert "sqlloader-script" in out and "delimited" not in out


def test_connectors_falls_back_to_beta():
    class Beta(StubISC):
        def get_all(self, path, params=None):
            if path == "/v3/connectors":
                raise ISCError("404")
            return [{"scriptName": "x-sql"}]
    assert discover.main(["connectors"], isc=Beta()) == 0


def test_no_match_exit_code():
    assert discover.main(["connectors", "--query", "zzz"], isc=StubISC()) == 1


def test_source_output_has_template_and_no_secret(capsys):
    isc = StubISC(sources=[SRC], schemas=SCHEMA)
    assert discover.main(["source", "--query", "HR Loader"], isc=isc) == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "connector: sqlloader-script" in out and "clusterId: cl1" in out
    assert "entitlementColumns:" in out and "- ROLE" in out
    assert all(m == "GET" for m, _ in isc.calls)


def test_source_needs_name():
    import pytest
    with pytest.raises(SystemExit):
        discover.main(["source"], isc=StubISC())


def test_errors_are_reported_not_raised(capsys):
    class Boom(StubISC):
        def get_all(self, path, params=None):
            raise ISCError("boom")
    assert discover.main(["sources"], isc=Boom()) == 2
