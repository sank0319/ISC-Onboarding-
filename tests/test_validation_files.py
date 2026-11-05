"""Negative tests for config sections, account files, sqlloader templates and AD exports."""
import re

import pytest

import onboard

HDR = "EmployeeID,UserName,Email,Status,Role"
ROW = "1001,u1,u1@example.com,Active,Approver"


def errors(app, defaults):
    return onboard.batch_errors([app], defaults)


# ---- delimited sections
@pytest.mark.parametrize("over", [
    {"delimited": None},
    {"delimited": {"accountFile": None}},
    {"delimited": {"accountFile": ""}},
    {"delimited": {"schema": None}},
    {"delimited": {"schema": {"identityAttribute": None}}},
    {"delimited": {"schema": {"displayAttribute": ""}}},
    {"delimited": {"schema": {"entitlementColumns": None}}},
    {"delimited": {"schema": {"entitlementColumns": []}}},
    {"delimited": {"schema": {"entitlementColumns": "Role"}}},
    {"delimited": {"correlation": None}},
    {"delimited": {"correlation": []}},
    {"delimited": {"correlation": [{"identityAttr": "uid"}]}},
    {"delimited": {"correlation": [{"accountAttr": "UserName"}]}},
    {"delimited": {"correlation": [{"identityAttr": "uid", "accountAttr": "NoSuchColumn"}]}},
    {"delimited": {"correlation": ["UserName"]}},
], ids=lambda o: str(o)[:70])
def test_missing_or_malformed_delimited_section(delim_app, defaults, over):
    assert errors(delim_app(**over), defaults)


# ---- account file shapes
FILES = {
    "missing-file": (None, "not found|does not exist|missing"),
    "empty-file": (b"", "header"),
    "header-only": (f"{HDR}\n".encode(), "no account rows|no rows"),
    "blank-lines-only": (b"\n\n\n", "header"),
    "duplicate-columns": (f"{HDR},Role\n{ROW},x\n".encode(), "duplicate"),
    "blank-column-name": (f"{HDR},\n{ROW},x\n".encode(), "blank|empty"),
    "padded-header": (f"EmployeeID ,UserName,Email,Status,Role\n{ROW}\n".encode(), "EmployeeID"),
    "identity-not-in-header": (f"Id,UserName,Email,Status,Role\n{ROW}\n".encode(), "EmployeeID"),
    "display-not-in-header": (f"EmployeeID,Name,Email,Status,Role\n{ROW}\n".encode(), "UserName"),
    "entitlement-not-in-header": (f"EmployeeID,UserName,Email,Status,Group\n{ROW}\n".encode(), "Role"),
    "wrong-delimiter": (f"{HDR.replace(',', ';')}\n{ROW.replace(',', ';')}\n".encode(), "EmployeeID"),
    "undecodable-bytes": (b"\xff\xfe\x00\x01garbage\n\x80\x81", "read|decode|header"),
}


@pytest.mark.parametrize("case", FILES)
def test_bad_account_file(delim_app, csv_file, defaults, tmp_path, case):
    raw, expected = FILES[case]
    path = str(tmp_path / "nope.csv") if raw is None else csv_file(raw=raw)
    errs = errors(delim_app(delimited={"accountFile": path}), defaults)
    assert errs and any(re.search(expected, e, re.I) for e in errs), errs


def test_directory_as_account_file(delim_app, defaults, tmp_path):
    assert errors(delim_app(delimited={"accountFile": str(tmp_path)}), defaults)


def test_bom_header_is_stripped(delim_app, csv_file, defaults):
    path = csv_file(raw=b"\xef\xbb\xbf" + f"{HDR}\n{ROW}\n".encode())
    assert onboard.read_csv(path)[0][0] == "EmployeeID"
    assert errors(delim_app(delimited={"accountFile": path}), defaults) == []


def test_custom_delimiter_is_honoured(delim_app, csv_file, defaults):
    path = csv_file(f"{HDR.replace(',', ';')}\n{ROW.replace(',', ';')}\n")
    over = {"delimited": {"accountFile": path, "connectorAttributes": {"delimiter": ";"}}}
    assert errors(delim_app(**over), defaults) == []


# ---- sqlloader
@pytest.fixture
def sql_app():
    return {"appName": "Sql", "connectorType": "sqlloader", "owner": "asmith", "template": "default"}


def test_sqlloader_placeholder_connector_rejected(sql_app, defaults):
    defaults["sqlLoaderTemplates"]["default"]["connector"] = "REPLACE_WITH_SQL_LOADER_SCRIPT_NAME"
    assert any("connector" in e for e in errors(sql_app, defaults))


@pytest.mark.parametrize("tweak", [
    lambda d, a: d["sqlLoaderTemplates"]["default"].update(connector=""),
    lambda d, a: d["sqlLoaderTemplates"]["default"].update(connector=None),
    lambda d, a: a.update(template="nope"),
    lambda d, a: d["sqlLoaderTemplates"]["default"]["schema"].pop("attributes"),
    lambda d, a: d["sqlLoaderTemplates"]["default"]["schema"].update(identityAttribute=""),
    lambda d, a: d["sqlLoaderTemplates"]["default"].update(correlation=[]),
    lambda d, a: d.pop("sqlLoaderTemplates"),
], ids=range(7))
def test_sqlloader_template_problems(sql_app, defaults, tweak):
    tweak(defaults, sql_app)
    assert errors(sql_app, defaults)


def test_sqlloader_valid_template_passes(sql_app, defaults):
    assert errors(sql_app, defaults) == []


# ---- AD export
@pytest.mark.parametrize("cell", ["", "<<>>", "<< ; >>", "not-an-email", "<<a@x.com; broken>>", "<<a b@x.com>>",
                                  "<<a@@x.com>>", "<<a@x.com,b@x.com>>", "<<@x.com>>", "<<a@x>>"])
def test_ad_export_with_bad_approvers(ad_app, defaults, cell):
    text = f'cn,distinguishedName,description,extensionAttribute10\nG1,"CN=G1,OU=T",d,"{cell}"\n'
    assert any("line 2" in e for e in errors(ad_app(text), defaults))


def test_ad_export_valid_and_duplicate_approvers_pass(ad_app, defaults):
    text = ('cn,distinguishedName,description,extensionAttribute10\n'
            'G1,"CN=G1,OU=T",d,<<A@x.com; a@x.com;; a@x.com>>\n')
    assert errors(ad_app(text), defaults) == []


@pytest.mark.parametrize("text", [
    "cn,distinguishedName,description\nG1,CN=G1,d\n",
    "cn,dn,description,extensionAttribute10\nG1,CN=G1,d,<<a@x.com>>\n",
    "cn,distinguishedName,description,extensionAttribute10\n",
    "",
    'cn,distinguishedName,description,extensionAttribute10\nG1,"",d,<<a@x.com>>\n',
], ids=["no-approval-col", "no-value-col", "header-only", "empty", "blank-dn"])
def test_ad_export_structure_problems(ad_app, defaults, text):
    assert errors(ad_app(text), defaults)


@pytest.mark.parametrize("over", [
    {"ad": {"sourceName": None}}, {"ad": {"entitlementExport": None}}, {"ad": {"entitlementExport": "/no/such.csv"}},
    {"ad": {"scopeRegex": "("}}, {"ad": {"scopeRegex": "[a-"}},
    {"ad": {"accessProfileNameTemplate": "{app}"}}, {"ad": {"accessProfileNameTemplate": "{nope} {name}"}},
], ids=lambda o: str(o))
def test_ad_section_problems(ad_app, defaults, over):
    assert errors(ad_app(**over), defaults)
