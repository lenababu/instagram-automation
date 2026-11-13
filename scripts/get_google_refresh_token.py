"""One-time local helper: obtain a Google OAuth refresh token for Drive (read) + Sheets.

Usage (on your laptop, once):
    pip install google-auth-oauthlib
    python scripts/get_google_refresh_token.py path/to/client_secret.json

Then copy the printed GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN into
GitHub repository secrets. The refresh token does not expire as long as the OAuth app's
publishing status is "In production" (Testing-mode tokens die after 7 days).
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    secret_file = sys.argv[1]
    flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    with open(secret_file, encoding="utf-8") as fh:
        client = json.load(fh)
    client = client.get("installed") or client.get("web")
    print("\nAdd these as GitHub secrets:\n")
    print(f"GOOGLE_CLIENT_ID={client['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={client['client_secret']}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
