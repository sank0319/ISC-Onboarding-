"""Access profile inputs, empty descriptions and the AD governance-group flow."""
import pytest

import onboard
from conftest import AD_TEXT, Returns, run_batch

AP = lambda *vals, name="Demo App - Approver", **kw: {"name": name, "entitlementValues": list(vals), **kw}  # noqa: E731


# ---- config shape
@pytest.mark.parametrize("aps", [
    [{"entitlementValues": ["Approver"]}], [{"name": "", "entitlementValues": ["Approver"]}],
    [{"name": "AP"}], [{"name": "AP", "entitlementValues": []}], [{"name": "AP", "entitlementValues": "Approver"}],
    [{"name": "AP", "entitlementValues": [1]}], [{"name": "AP", "entitlementValues": [""]}], ["AP"], {"name": "AP"},
    [AP("Approver", name="Dup"), AP("Viewer", name="dup")],
], ids=range(10))
def test_malformed_access_profile_config(delim_app, defaults, aps):
    assert onboard.batch_errors([delim_app(accessProfiles=aps)], defaults)


# ---- values that do not exist
@pytest.mark.parametrize("values", [["Nope"], ["Approver", "Nope"], ["nope", "NOPE"]], ids=repr)
def test_unknown_entitlement_values_are_reported(isc, run_one, delim_app, values):
    r = run_one(delim_app(accessProfiles=[AP(*values)]))
    assert r["status"] == "partial" and "Nope".lower() in str(r).lower()
    assert not isc.aps, "an access profile with fewer entitlements than requested must not be created"
    assert isc.apps, "application is kept"


@pytest.mark.parametrize("values", [["approver"], ["APPROVER"], ["Approver", "approver"]], ids=repr)
def test_values_match_case_insensitively_and_dedupe(isc, run_one, delim_app, values):
    assert run_one(delim_app(accessProfiles=[AP(*values)]))["status"] == "ok"
    assert len(next(iter(isc.aps.values()))["entitlements"]) == 1


def test_no_access_profiles_is_fine(isc, run_one, delim_app):
    assert run_one(delim_app(accessProfiles=None))["status"] == "ok" and not isc.aps


def test_no_entitlements_after_aggregation_with_profiles_is_not_ok(isc, run_one, delim_app):
    isc.fail("GET", r"/beta/entitlements$", Returns([]))
    assert run_one(delim_app())["status"] != "ok"


# ---- empty descriptions (ISC answers 400 "Required field description was missing or empty")
@pytest.mark.parametrize("desc", ["", "   ", None, "\n"], ids=repr)
def test_empty_description_is_replaced_by_default(isc, run_one, delim_app, desc):
    r = run_one(delim_app(description=desc, accessProfiles=[AP("Approver", description=desc)]))
    assert r["status"] == "ok", r
    assert all(a["description"].strip() for a in [*isc.apps.values(), *isc.aps.values(), *isc.sources.values()])


@pytest.mark.parametrize("desc", ["", "  "])
def test_ad_row_with_blank_description_still_gets_a_profile(ad_app, run_one, isc, desc):
    text = AD_TEXT.replace(",d1,", f",{desc},")
    assert run_one(ad_app(text))["status"] == "ok" and len(isc.aps) == 2


# ---- AD flow
def test_ad_happy_path_builds_groups_profiles_and_application(ad_app, run_one, isc):
    r = run_one(ad_app())
    assert r["accessProfilesCreated"] == 2 and r["status"] == "ok"
    assert sorted(len(g["members"]) for g in isc.groups.values()) == [1, 2]
    assert len(next(iter(isc.apps.values()))["accessProfiles"]) == 2


def row(approvers, dn="CN=G1,OU=Treasury,DC=x", cn="G1"):
    return f'cn,distinguishedName,description,extensionAttribute10\n{cn},"{dn}",d,"{approvers}"\n'


@pytest.mark.parametrize("approvers", ["<<ghost@example.com>>", "<<alice.tan@example.com; ghost@example.com>>",
                                       "<<twin@example.com>>"], ids=["unknown", "one-unknown", "ambiguous"])
def test_unresolvable_approver_skips_profile_and_marks_partial(ad_app, run_one, isc, approvers):
    r = run_one(ad_app(row(approvers)))
    assert r["status"] == "partial" and r["skipped"][0]["unresolvedApprovers"] and not isc.aps and not isc.groups


def test_duplicate_approver_emails_yield_single_member(ad_app, run_one, isc):
    assert run_one(ad_app(row("<<Alice.Tan@example.com; alice.tan@example.com>>")))["status"] == "ok"
    assert len(next(iter(isc.groups.values()))["members"]) == 1


def test_export_rows_missing_in_isc_are_not_silently_ok(ad_app, run_one, isc):
    r = run_one(ad_app(row("<<alice.tan@example.com>>", dn="CN=Ghost,OU=Treasury,DC=x")))
    assert r["status"] == "partial" and r["exportRowsNotFound"] == 1 and not isc.apps


def test_scope_regex_filters_rows(ad_app, run_one, isc):
    r = run_one(ad_app(ad={"scopeRegex": "G2"}))
    assert r["accessProfilesCreated"] == 1 and len(isc.groups) == 1


def test_ad_source_missing_fails(ad_app, run_one, isc):
    r = run_one(ad_app(ad={"sourceName": "Nope"}))
    assert r["status"] == "failed" and "AD source not found" in r["error"]


def test_rows_producing_the_same_profile_name_are_not_merged(ad_app, run_one, isc):
    text = AD_TEXT.replace('G2,"CN=G2', 'G1,"CN=G2')
    r = run_one(ad_app(text))
    assert r["status"] == "partial" and len(isc.aps) == 1 and r["skipped"]


def test_long_names_truncating_to_one_group_name_do_not_share_the_group(ad_app, run_one, isc, defaults):
    defaults["ad"]["governanceGroupNameMaxLength"] = 10
    src = next(s["id"] for s in isc.sources.values() if s["name"] == "Active Directory")
    for n in (1, 2):
        isc.add_entitlement(src, f"CN=LONGLONGLONG{n},OU=Treasury,DC=x")
    text = AD_TEXT.replace("G1,", "LONGLONGLONG1,").replace("G2,", "LONGLONGLONG2,")
    r = run_one(ad_app(text, ad={"accessProfileNameTemplate": "{name}"}))
    assert r["status"] == "partial" and len(isc.groups) == 1 and len(isc.aps) == 1


@pytest.mark.parametrize("fault,status", [(("POST", "/beta/workgroups$"), "failed"),
                                          (("POST", "bulk-add"), "failed"),
                                          (("POST", "/v3/access-profiles"), "failed")])
def test_ad_api_failures_fail_the_app_and_batch_continues(ad_app, delim_app, isc, defaults, fault, status):
    isc.fail(*fault, 500, times=1)
    first, second = run_batch(isc, defaults, [ad_app(), delim_app()])
    assert first["status"] == status and second["status"] == "ok"


def test_governance_group_owner_unresolved(ad_app, run_one, isc, defaults):
    defaults["ad"]["governanceGroupOwner"] = "ghost"
    r = run_one(ad_app())
    assert r["status"] == "failed" and not isc.groups


def test_existing_group_members_are_synced_only_when_enabled(ad_app, run_one, isc, defaults):
    run_one(ad_app())
    gid = next(iter(isc.groups))
    stranger = isc.add_identity("stranger", "s@example.com")
    isc.groups[gid]["members"].append(stranger)
    run_one(ad_app())
    assert stranger in isc.groups[gid]["members"]
    defaults["ad"]["syncRemoveGovernanceGroupMembers"] = True
    run_one(ad_app())
    assert stranger not in isc.groups[gid]["members"]
