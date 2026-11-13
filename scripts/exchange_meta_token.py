"""Exchange a short-lived Meta user token for a 60-day long-lived one, and find your IG user id.

Usage:
    python scripts/exchange_meta_token.py APP_ID APP_SECRET SHORT_LIVED_TOKEN [API_VERSION]

Prints META_ACCESS_TOKEN (long-lived) and the INSTAGRAM_USER_ID of every Instagram
Business account linked to your Facebook Pages.
"""
import sys

import requests


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    app_id, app_secret, short = sys.argv[1:4]
    ver = sys.argv[4] if len(sys.argv) > 4 else "v24.0"
    base = f"https://graph.facebook.com/{ver}"

    r = requests.get(
        f"{base}/oauth/access_token",
        params={"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
                "fb_exchange_token": short},
        timeout=30,
    ).json()
    if "access_token" not in r:
        print("Exchange failed:", r)
        sys.exit(1)
    long_token = r["access_token"]
    print(f"\nMETA_ACCESS_TOKEN={long_token}\n(expires in ~{r.get('expires_in', 0)//86400} days)\n")

    pages = requests.get(f"{base}/me/accounts", params={"access_token": long_token,
                         "fields": "name,instagram_business_account{id,username}"}, timeout=30).json()
    for p in pages.get("data", []):
        ig = p.get("instagram_business_account")
        if ig:
            print(f"Page '{p['name']}' -> INSTAGRAM_USER_ID={ig['id']}  (@{ig.get('username')})")
    if not any(p.get("instagram_business_account") for p in pages.get("data", [])):
        print("No Instagram Business account linked to your Pages. Link it in Instagram settings first.")


if __name__ == "__main__":
    main()
