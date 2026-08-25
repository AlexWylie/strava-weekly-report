#!/usr/bin/env python3
"""
Diagnostic: attempt a Strava token refresh exactly as the workflow does,
but print the full response so we can see WHY it fails (the main script's
raise_for_status() hides Strava's error body).

Usage — paste the SAME three values you put in the GitHub secrets:
    python verify_strava.py
"""

import requests

client_id     = input("STRAVA_CLIENT_ID:     ").strip()
client_secret = input("STRAVA_CLIENT_SECRET: ").strip()
refresh_token = input("STRAVA_REFRESH_TOKEN: ").strip()

print("\nRequesting token refresh...\n")
r = requests.post("https://www.strava.com/oauth/token", data={
    "client_id":     client_id,
    "client_secret": client_secret,
    "grant_type":    "refresh_token",
    "refresh_token": refresh_token,
})

print(f"HTTP {r.status_code}")
print("Response body:")
print(r.text)

if r.status_code == 200:
    print("\n✅ Credentials work. If the workflow still fails, the GitHub secrets")
    print("   don't match these values exactly (check for typos / trailing spaces).")
else:
    print("\n❌ Strava rejected these credentials. The error body above says why.")
