"""Connectivity test: load credentials (Vault or env), get an ISC token, list 5 source names.

Prints only source names. Exit code 1 with a one-line message on SecretsError / ISCError.
"""
import sys

from isc_client import ISCClient, ISCError
from secrets_store import SecretsError, load_credentials


def main():
    try:
        creds, source = load_credentials()
        print(f"Credentials loaded from: {source}")
        isc = ISCClient(creds["ISC_TENANT"], creds["ISC_CLIENT_ID"], creds["ISC_CLIENT_SECRET"],
                        base_url=creds.get("ISC_BASE_URL") or None)
        print(f"Calling {isc.base}")
        sources = isc.request("GET", "/v3/sources", params={"limit": 5}) or []
    except SecretsError as e:
        print(f"ERROR (credentials): {e}", file=sys.stderr)
        return 1
    except ISCError as e:
        print(f"ERROR (ISC): {str(e)[:300]}", file=sys.stderr)
        return 1
    print(f"Token received; {len(sources)} source(s):")
    for s in sources:
        print(f"  {s.get('name')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
