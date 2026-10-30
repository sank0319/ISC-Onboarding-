"""Filter escaping and owner resolution against the fake ISC."""
import pytest

from conftest import Returns, parse_filter

SPECIAL = ["plain", "O'Brien", 'Say "Hi"', "back\\slash", "trailing\\", 'x\\" or name sw \\"',
           'a" or name sw "', "日本語", "(?i).*", "tab\tname", "name eq \"x\""]


@pytest.mark.parametrize("name", SPECIAL, ids=repr)
def test_find_one_filter_round_trips_special_characters(ob, isc, name):
    ob.find_one("/v3/sources", name)
    assert parse_filter(isc.calls[-1].params["filters"]) == ("name", name)


@pytest.mark.parametrize("name", [n for n in SPECIAL if "\t" not in n], ids=repr)
def test_special_character_app_name_is_created_once(run_one, delim_app, isc, name):
    app = delim_app(appName=name, application=f"App {name}")
    assert run_one(app)["status"] == "ok"
    assert run_one(app)["status"] == "ok"
    assert [s["name"] for s in isc.sources.values()] == [name]


@pytest.mark.parametrize("key,field", [("asmith", "alias"), ("asmith@example.com", "email")])
def test_owner_alias_vs_email_lookup(ob, isc, key, field):
    assert ob.identity_ref(key)["id"]
    assert parse_filter(isc.calls[-1].params["filters"])[0] == field


@pytest.mark.parametrize("owner,count", [("ghost", 0), ("ghost@example.com", 0), ("twin", 2), ("twin@example.com", 2),
                                         ("@", 0), ("a@", 0), ("asmith@@example.com", 0), ("ASMITH", 0), ("a\\", 0)])
def test_owner_must_resolve_to_exactly_one_identity(run_one, delim_app, isc, owner, count):
    r = run_one(delim_app(owner=owner))
    assert r["status"] == "failed" and f"resolved to {count} identities" in r["error"]
    assert isc.writes == [] and not isc.sources


@pytest.mark.parametrize("payload", [None, [], {"detail": "nope"}, "<html>", [{"name": "no id"}]], ids=repr)
def test_owner_lookup_malformed_responses_fail_cleanly(run_one, delim_app, isc, payload):
    isc.fail("GET", "public-identities", Returns(payload))
    r = run_one(delim_app())
    assert r["status"] == "failed" and isc.writes == []


def test_owner_lookup_is_cached(ob, isc):
    ob.identity_ref("asmith"), ob.identity_ref("asmith")
    assert len(isc.called("GET", "public-identities")) == 1


def test_existing_source_with_bad_owner_uploads_nothing(run_one, delim_app, isc):
    isc.seed_source("Demo-App")
    r = run_one(delim_app(owner="ghost"))
    assert r["status"] == "failed"
    assert not isc.called("POST", "load-accounts")
