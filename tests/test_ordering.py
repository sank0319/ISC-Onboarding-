"""Ordering and size assumptions: bulk-update chunks, paging, step order, server ordering."""
import pytest

from conftest import run_batch


def many_roles(n):
    return "EmployeeID,UserName,Email,Status,Role\n" + "".join(f"{i},u{i},u{i}@x.com,Active,R{i:03}\n" for i in range(n))


@pytest.mark.parametrize("n,batches", [(1, 1), (49, 1), (50, 1), (51, 2), (100, 2), (101, 3)])
def test_bulk_update_chunks_never_exceed_50(isc, run_one, delim_app, csv_file, n, batches):
    r = run_one(delim_app(delimited={"accountFile": csv_file(many_roles(n))}, accessProfiles=None))
    sizes = [len(c.body["entitlementIds"]) for c in isc.called("POST", "bulk-update")]
    assert r["status"] == "ok" and len(sizes) == batches and max(sizes) <= 50 and sum(sizes) == n
    assert all(e["requestable"] for e in isc.ents.values())


@pytest.mark.parametrize("n", [249, 250, 251, 501])
def test_all_entitlements_are_seen_across_page_boundaries(isc, run_one, delim_app, csv_file, n):
    r = run_one(delim_app(delimited={"accountFile": csv_file(many_roles(n))}, accessProfiles=None))
    assert r["entitlementsRequestable"] == n and len(isc.called("PUT", "entitlement-request-config")) == n


def test_result_does_not_depend_on_server_ordering(isc, defaults, delim_app):
    run_batch(isc, defaults, [delim_app()])
    isc.ents = dict(reversed(list(isc.ents.items())))
    isc.calls.clear()
    assert run_batch(isc, defaults, [delim_app()])[0]["status"] == "ok" and len(isc.aps) == 1


def test_step_order_is_schema_correlation_upload_requestable_app_profile(isc, run_one, delim_app):
    run_one(delim_app())
    order = ["PUT schemas/", "PUT correlation-config", "POST load-accounts", "POST bulk-update",
             "PUT entitlement-request-config", "POST /beta/source-apps", "POST /v3/access-profiles"]
    seen = [f"{c.method} {c.path}" for c in isc.calls if c.method != "GET"]
    idx = [next(i for i, s in enumerate(seen) if frag.split(" ", 1)[0] == s.split(" ", 1)[0] and frag.split(" ", 1)[1] in s)
           for frag in order]
    assert idx == sorted(idx)


def test_results_follow_input_order_and_failures_do_not_skip_later_apps(isc, defaults, delim_app):
    apps = [delim_app(appName=n, application=f"App {n}", owner=o,
                      accessProfiles=[{"name": f"AP {n}", "entitlementValues": ["Viewer"]}])
            for n, o in [("C", "asmith"), ("A", "ghost"), ("B", "jdoe")]]
    results = run_batch(isc, defaults, apps)
    assert [(r["app"], r["status"]) for r in results] == [("C", "ok"), ("A", "failed"), ("B", "ok")]


def test_failed_app_does_not_poison_the_owner_cache(isc, defaults, delim_app):
    first = delim_app(appName="X", application="X1", owner="ghost")
    second = delim_app(appName="Y", application="Y1", owner="ghost")
    assert [r["status"] for r in run_batch(isc, defaults, [first, second])] == ["failed", "failed"]
