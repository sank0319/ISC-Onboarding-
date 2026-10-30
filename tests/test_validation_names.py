"""Negative tests for names, owners and batch-level duplicates (no API calls)."""
import pytest

import onboard

LONG = "a" * 129
BAD_NAMES = ["", "   ", None, "\t", LONG, "line\nbreak", "tab\there", " lead", "trail ", 123, ["x"], {"a": 1}]
BAD_OWNERS = ["", "  ", None, 123, ["asmith"], {"a": 1}, " asmith", "asmith "]
# characters that are legal and must survive untouched (quotes, backslash, unicode, filter/regex syntax)
OK_NAMES = ["O'Brien", 'Say "Hi"', "back\\slash", "日本語アプリ", "x\" or name sw \"", "(?i).*", "a" * 128,
            'name eq "x"', "emoji \U0001F600 app", "semi;colon"]


def errors(app, defaults):
    return onboard.batch_errors([app], defaults)


BAD_NAME_CASES = [(f, b) for f in ("appName", "application", "sourceName") for b in BAD_NAMES
                  if not (b is None and f != "appName")]


@pytest.mark.parametrize("field,bad", BAD_NAME_CASES, ids=lambda v: repr(v)[:16])
def test_bad_name_is_rejected(delim_app, defaults, field, bad):
    assert errors(delim_app(**{field: bad}), defaults)


@pytest.mark.parametrize("bad", BAD_OWNERS, ids=lambda v: repr(v)[:16])
def test_bad_owner_is_rejected(delim_app, defaults, bad):
    assert errors(delim_app(owner=bad), defaults)


@pytest.mark.parametrize("field", ["appName", "application", "sourceName"])
@pytest.mark.parametrize("ok", OK_NAMES, ids=lambda v: repr(v)[:16])
def test_odd_but_legal_names_are_accepted(delim_app, defaults, field, ok):
    assert errors(delim_app(**{field: ok}), defaults) == []


@pytest.mark.parametrize("bad", [None, "", "  ", ["x"], 5], ids=lambda v: repr(v)[:16])
def test_ad_app_requires_application_name(ad_app, defaults, bad):
    assert errors(ad_app(application=bad), defaults)


@pytest.mark.parametrize("bad", ["ldap", "Delimited", "SQLLOADER", "", None, ["ad"], 5, "delimited "], ids=lambda v: repr(v)[:16])
def test_invalid_connector_type(delim_app, defaults, bad):
    assert any("connectorType" in e for e in errors(delim_app(connectorType=bad), defaults))


@pytest.mark.parametrize("entry", ["just a string", None, 5, [], ["a"]], ids=lambda v: repr(v)[:16])
def test_non_mapping_app_entries_give_errors_not_crashes(defaults, entry):
    assert onboard.batch_errors([entry], defaults)


def test_missing_connector_mapping_in_defaults(delim_app, defaults):
    defaults.pop("connectors")
    assert any("connectors" in e for e in errors(delim_app(), defaults))


# ---- duplicates inside one batch
DUP_NAMES = [("Same", "Same"), ("Same", "same"), ("Same", "SAME"), ("Same", " Same"), ("Same", "Same "),
             ("My App", "My  App"), ("My App", "my\tapp"), ("Café", "CAFÉ")]


@pytest.mark.parametrize("a,b", DUP_NAMES, ids=lambda v: repr(v)[:16])
def test_duplicate_app_names_in_batch(delim_app, defaults, a, b):
    errs = onboard.batch_errors([delim_app(appName=a, application="A1"), delim_app(appName=b, application="A2")], defaults)
    assert any("duplicate" in e for e in errs)


def test_distinct_similar_names_are_not_duplicates(delim_app, defaults):
    apps = [delim_app(appName=n, application=f"App {n}") for n in ("App-1", "App-10", "App1")]
    assert onboard.batch_errors(apps, defaults) == []


def test_duplicate_application_name_across_apps(delim_app, defaults):
    apps = [delim_app(appName="A", application="Shared"), delim_app(appName="B", application="shared")]
    assert any("duplicate" in e for e in onboard.batch_errors(apps, defaults))


def test_source_name_colliding_with_other_app_name(delim_app, defaults):
    apps = [delim_app(appName="A", application="A1"), delim_app(appName="B", application="B1", sourceName="a")]
    assert any("duplicate" in e for e in onboard.batch_errors(apps, defaults))


def test_two_apps_without_appname_report_errors(defaults):
    errs = onboard.batch_errors([{"connectorType": "ad"}, {"connectorType": "ad"}], defaults)
    assert any("appName is required" in e for e in errs)
