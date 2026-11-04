"""Active Directory connector: no source creation. Per in-scope group in the entitlement export:
governance group (named after the access profile) with the approvers from the extension attribute
(<<email1; email2>>) -> access profile (L1 Manager -> L2 that governance group) -> application."""
import logging
import re

from common import norm, read_csv, text_or
from connectors import Connector, register
from isc_client import ISCError
from validation import check_text, read_csv_checked

log = logging.getLogger("onboard")

EMAIL_RE = re.compile(r"^[^@\s;<>]+@[^@\s;<>]+\.[^@\s;<>]+$")


def parse_approvers(raw):
    """AD extension attribute '<<a@x.com; b@x.com>>' -> ['a@x.com', 'b@x.com'] (deduped, lower-case).

    Raises ValueError if empty or if any entry is not a valid email address.
    """
    cleaned = re.sub(r"[<>]", "", raw or "").strip()
    emails = list(dict.fromkeys(e.strip().lower() for e in cleaned.split(";") if e.strip()))
    if not emails:
        raise ValueError("approver attribute is empty - level 2 approver is mandatory")
    bad = [e for e in emails if not EMAIL_RE.match(e)]
    if bad:
        raise ValueError(f"invalid approver email(s): {bad}")
    return emails


def gov_group_name(ap_name, max_len):
    name = re.sub(r"\s+", " ", ap_name).strip()
    return name[:max_len].rstrip() if max_len else name


@register
class ActiveDirectory(Connector):
    type = "ad"
    creates_source = False

    def validate(self, app, defaults):
        errs, ad, adc = [], app.get("ad") or {}, defaults.get("ad") or {}
        for k in ("sourceName", "entitlementExport"):
            if not ad.get(k):
                errs.append(f"ad.{k} missing")
        check_text(errs, app, "application", "application (ISC app name)")
        tmpl = ad.get("accessProfileNameTemplate", adc.get("accessProfileNameTemplate", "{app} - {name}"))
        try:
            if "{name}" not in tmpl:
                errs.append("ad.accessProfileNameTemplate must contain {name}")
            tmpl.format(app="a", name="b")
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as e:
            errs.append(f"ad.accessProfileNameTemplate invalid: {e!r}")
        try:
            re.compile(ad.get("scopeRegex") or "")
        except (re.error, TypeError) as e:
            errs.append(f"ad.scopeRegex invalid: {e}")
        exp = ad.get("entitlementExport")
        if not exp:
            return errs
        cols, rows, file_errs = read_csv_checked(exp, ad.get("delimiter", ","), "entitlement export")
        vcol = ad.get("valueColumn", adc.get("valueColumn"))
        acol = ad.get("approvalAttribute", adc.get("approvalAttribute"))
        if file_errs:
            return errs + file_errs
        errs += [f"column '{c}' not in {exp} (has {cols})" for c in (vcol, acol) if c not in cols]
        if not rows:
            errs.append(f"{exp} has no rows")
        if vcol in cols and acol in cols:
            for i, r in enumerate(rows, start=2):
                try:
                    if not (r.get(vcol) or "").strip():
                        raise ValueError(f"empty {vcol}")
                    parse_approvers(r.get(acol))
                except ValueError as e:
                    errs.append(f"{exp} line {i} ({(r.get(vcol) or '')[:60]}): {e}")
        return errs

    def feed(self, app):
        ad = app.get("ad")
        return (ad.get("entitlementExport") if isinstance(ad, dict) else None), f"{app.get('appName')}-group.csv"

    def handle(self, ob, app, result):
        ad, adc = app["ad"], ob.d.get("ad") or {}
        source = ob.find_one("/v3/sources", ad["sourceName"])
        if not source:
            raise ISCError(f"AD source not found: {ad['sourceName']}")
        vcol = ad.get("valueColumn", adc.get("valueColumn"))
        ncol = ad.get("nameColumn", adc.get("nameColumn"))
        acol = ad.get("approvalAttribute", adc.get("approvalAttribute"))
        tmpl = ad.get("accessProfileNameTemplate", adc.get("accessProfileNameTemplate", "{app} - {name}"))
        max_len = adc.get("governanceGroupNameMaxLength", 128)
        sync_remove = adc.get("syncRemoveGovernanceGroupMembers", False)
        scope = ad.get("scopeRegex")  # optional extra filter on the value column (e.g. OU)

        # Governance group owner: configured identity (e.g. IAM service identity) or the app owner
        owner = ob.identity_ref(app["owner"])
        gg_owner_key = ad.get("governanceGroupOwner") or adc.get("governanceGroupOwner")
        gg_owner = ob.identity_ref(gg_owner_key) if gg_owner_key else owner

        _, rows = read_csv(ad["entitlementExport"], ad.get("delimiter", ","))
        if scope:
            rows = [r for r in rows if re.search(scope, r[vcol], re.IGNORECASE)]
        ents = {e["value"].lower(): e for e in ob.source_entitlements(source["id"])}

        ap_ids, created, existing, missing, skipped, claimed = [], 0, 0, [], [], {}
        for r in rows:
            ent = ents.get(r[vcol].strip().lower())
            if not ent:
                missing.append(r[vcol])
                continue
            ap_name = tmpl.format(app=app["appName"], name=(r.get(ncol) or ent["name"]).strip())
            gg_name = gov_group_name(ap_name, max_len)
            keys = [("ap", norm(ap_name)), ("gg", norm(gg_name))]
            if any(claimed.get(k, ent["id"]) != ent["id"] for k in keys):
                log.error("  SKIP AP '%s': name collides with another group's access profile/governance group", ap_name)
                skipped.append({"accessProfile": ap_name, "reason": "name collision"})
                continue
            claimed.update(dict.fromkeys(keys, ent["id"]))

            # 1. approvers from extension attribute -> ISC identities (all must resolve)
            emails = parse_approvers(r.get(acol))
            resolved = {e: ob.approver_identity_id(e) for e in emails}
            unresolved = [e for e, i in resolved.items() if not i]
            if unresolved:
                ob.note("access_profile_skipped", "  SKIP AP '%s': approver(s) not found / ambiguous in ISC: %s",
                        ap_name, unresolved, level=logging.ERROR, step="approvers")
                skipped.append({"accessProfile": ap_name, "unresolvedApprovers": unresolved})
                continue

            # 2. governance group named after the access profile, members = approvers
            gg_id, _ = ob.ensure_governance_group(gg_name, gg_owner, list(resolved.values()), sync_remove)

            # 3. access profile: L1 Manager, L2 governance group
            schemes = [{"approverType": "MANAGER", "approverId": None},
                       {"approverType": "GOVERNANCE_GROUP", "approverId": gg_id}]
            desc = text_or(r.get("description"), f"{app['appName']} access via AD group {ent['name']}")
            ap_id, state = ob.ensure_access_profile(ap_name, desc, owner, source, [ent["id"]], schemes)
            ap_ids.append(ap_id)
            created += state == "created"
            existing += state == "existing"
            log.info("  AP '%s' [%s] approvals: Manager -> GG '%s' (%d approver(s))",
                     ap_name, state, ap_name, len(emails))

        if missing:
            ob.note("export_rows_missing", "  %d export rows not found in ISC (aggregate AD first?): %s",
                    len(missing), missing[:5], level=logging.WARNING, step="entitlements")
        if ap_ids:
            ob.ensure_source_app(app["application"], app.get("description", ""), owner, source, ap_ids)
        result.update(accessProfilesCreated=created, accessProfilesExisting=existing,
                      exportRowsNotFound=len(missing), skipped=skipped)
        if skipped or missing:
            result["status"] = "partial"
