#!/usr/bin/env python3
"""Bulk onboarding of applications into SailPoint ISC (run from GitHub Actions).

Connector handlers live one per file in onboarding/scripts/connectors/ (ad, delimited, sqlloader);
each connector has apps/ (app definitions) and data/ (feed files) in onboarding/connectors/<type>/.

Usage
  python onboard.py --validate-only
  python onboard.py --dry-run
  python onboard.py --apps "onboarding/connectors/delimited/apps/**/*.yaml" --wave 1
  python onboard.py --apps-csv docs/examples/apps.csv
"""
import argparse
import json
import logging
import os
import sys

from batch import batch_errors, layout_errors, load_apps, select_apps, target_env, with_env
from common import load_yaml
from isc_client import ISCClient
from onboarder import Onboarder
from run_log import RunLog, register_secret
from secrets_store import load_credentials

log = logging.getLogger("onboard")

DEFAULT_APPS = "onboarding/connectors/*/apps/**/*.yaml"


def write_summary(results, dry_run, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    md = os.environ.get("GITHUB_STEP_SUMMARY")
    if md:
        with open(md, "a", encoding="utf-8") as f:
            f.write(f"## ISC onboarding {'(DRY-RUN)' if dry_run else ''}\n\n"
                    "| App | Type | Status | Details |\n|---|---|---|---|\n")
            for r in results:
                details = {k: v for k, v in r.items() if k not in ("app", "type", "status")}
                f.write(f"| {r['app']} | {r['type']} | {r['status']} | {json.dumps(details)} |\n")



def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apps", default=DEFAULT_APPS, help="glob of app config files (recursive **)")
    p.add_argument("--wave", type=int, help="only configs in wave-<N> folders (e.g. delimited: 1 = pilot, 2 = rest)")
    p.add_argument("--apps-csv", help="CSV (applicationName,applicationType) selecting which apps to onboard")
    p.add_argument("--defaults", default="onboarding/config/defaults.yaml")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--report", default="onboarding-report.json")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    defaults = load_yaml(args.defaults)
    entries = load_apps(args.apps, args.wave)
    if args.apps_csv:
        entries, errors = select_apps(entries, args.apps_csv)
        if errors:
            print("Selection failed:\n  " + "\n  ".join(errors))
            sys.exit(1)
    env = target_env(os.environ.get("ISC_TARGET"))  # {env} in feed paths -> sandbox | production
    apps = [with_env(a, env) for _, a in entries]
    errors = layout_errors(entries) + batch_errors(apps, defaults)
    if errors:
        print("Validation failed:\n  " + "\n  ".join(errors))
        sys.exit(1)
    log.info("Validated %d app(s)", len(apps))
    if args.validate_only:
        return

    runlog = RunLog(os.environ.get("ISC_LOG_DIR") or "onboarding/logs",
                    target=os.environ.get("ISC_TARGET"), dry_run=args.dry_run)
    results, crashed = [], False
    try:
        creds, source = load_credentials()  # HashiCorp Vault when VAULT_ADDR is set, else ISC_* env
        register_secret(creds["ISC_CLIENT_ID"])
        register_secret(creds["ISC_CLIENT_SECRET"])
        runlog.event("secrets_loaded", source=source,
                     vault_path=os.environ.get("VAULT_SECRET_PATH") if source != "env" else None)
        isc = ISCClient(creds["ISC_TENANT"], creds["ISC_CLIENT_ID"],
                        creds["ISC_CLIENT_SECRET"], dry_run=args.dry_run,
                        base_url=creds.get("ISC_BASE_URL") or None, on_event=runlog.event)
        ob = Onboarder(isc, defaults, runlog)
        results = [ob.run(a) for a in apps]
        write_summary(results, args.dry_run, args.report)
    except BaseException as e:  # also Ctrl-C / cancellation; re-raised after logging
        crashed = True
        runlog.failure("run_crashed", e)
        raise
    finally:
        runlog.end(results, crashed)
    failed = [r["app"] for r in results if r["status"] != "ok"]  # failed or partial
    log.info("Done: %d ok, %d failed %s", len(results) - len(failed), len(failed), failed or "")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
