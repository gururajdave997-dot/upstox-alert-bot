"""Run locally each morning to get a fresh Upstox ACCESS_TOKEN.
Usage: python get_token.py   (set UPSTOX_API_KEY, UPSTOX_SECRET, UPSTOX_REDIRECT_URI or enter when asked)
"""
import os
from urllib.parse import parse_qs, quote, urlparse

import requests

key = os.getenv("UPSTOX_API_KEY") or input("API key: ").strip()
secret = os.getenv("UPSTOX_SECRET") or input("API secret: ").strip()
redirect = os.getenv("UPSTOX_REDIRECT_URI") or input("Redirect URI (as set in Upstox app): ").strip()

print("\n1) Open this URL, log in to Upstox:\n")
print(f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code"
      f"&client_id={key}&redirect_uri={quote(redirect, safe='')}\n")
raw = input("2) Paste the full redirected URL (or just the code): ").strip()
code = parse_qs(urlparse(raw).query).get("code", [raw])[0]

r = requests.post("https://api.upstox.com/v2/login/authorization/token",
                  headers={"accept": "application/json",
                           "Content-Type": "application/x-www-form-urlencoded"},
                  data={"code": code, "client_id": key, "client_secret": secret,
                        "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=15)
if r.ok and "access_token" in r.json():
    print("\nACCESS_TOKEN =\n" + r.json()["access_token"])
    print("\nPaste this into Railway > Variables > ACCESS_TOKEN (bot restarts automatically).")
else:
    print("Failed:", r.status_code, r.text)
