#!/usr/bin/env python3
"""
One-time Strava OAuth setup.
Run this locally to get a fresh refresh token, then update the
STRAVA_REFRESH_TOKEN secret in GitHub Actions.

Usage:
    STRAVA_CLIENT_ID=your_id STRAVA_CLIENT_SECRET=your_secret python strava_auth_setup.py
"""

import os
import webbrowser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    CLIENT_ID     = input("Enter your Strava Client ID: ").strip()
    CLIENT_SECRET = input("Enter your Strava Client Secret: ").strip()

REDIRECT_URI = "http://localhost:8000"
AUTH_URL = (
    f"https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&response_type=code"
    f"&approval_prompt=force"
    f"&scope=activity:read_all"
)

auth_code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        if auth_code:
            self.wfile.write(b"<h2>Authorised. You can close this tab.</h2>")
        else:
            self.wfile.write(b"<h2>No code received. Check the URL and try again.</h2>")

    def log_message(self, format, *args):
        pass  # suppress request logging


print("\nOpening Strava authorisation page in your browser...")
webbrowser.open(AUTH_URL)
print("Waiting for callback on http://localhost:8000 ...")

server = HTTPServer(("localhost", 8000), CallbackHandler)
server.handle_request()

if not auth_code:
    print("ERROR: No authorisation code received.")
    raise SystemExit(1)

print("Exchanging code for tokens...")
r = requests.post("https://www.strava.com/oauth/token", data={
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code":          auth_code,
    "grant_type":    "authorization_code",
})
r.raise_for_status()
tokens = r.json()

refresh_token = tokens["refresh_token"]

print("\n" + "=" * 60)
print("SUCCESS. Update your GitHub secret with the value below:")
print()
print(f"  Secret name:  STRAVA_REFRESH_TOKEN")
print(f"  Secret value: {refresh_token}")
print()
print("Go to: GitHub → strava-weekly-report → Settings → Secrets → Actions")
print("=" * 60 + "\n")
