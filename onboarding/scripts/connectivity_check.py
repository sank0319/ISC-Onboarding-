#!/usr/bin/env python3
"""Read-only connectivity test: load credentials, get a token, list 5 sources (names only)."""
import sys

from isc_client import ISCClient, ISCError
from secrets_store import SecretsError, load_credentials


def main():
    try:
        creds, source = load_credentials()
        isc = ISCClient(creds["ISC_TENANT"], creds["ISC_CLIENT_ID"], creds["ISC_CLIENT_SECRET"],
                        base_url=creds.get("ISC_BASE_URL"))
        print(f"Credentials loaded from {source}; calling {isc.base}")
        names = [s["name"] for s in isc.request("GET", "/v3/sources", params={"limit": 5}) or []]
    except (SecretsError, ISCError) as e:
        sys.exit(f"Connectivity test failed: {e}")
    print("Token OK. Sources:", *names, sep="\n  ")


if __name__ == "__main__":
    main()
