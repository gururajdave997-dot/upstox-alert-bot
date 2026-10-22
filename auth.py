"""Upstox login without a web server (works inside GitHub Actions).

Flow: bot sends a login link on Telegram -> you log in -> Upstox redirects to
UPSTOX_REDIRECT_URI?code=XXXX -> the code reaches the bot (one tap via the GitHub
Pages helper page, or you paste the code/URL) -> bot exchanges it for today's token.
The token is stored encrypted (key derived from UPSTOX_SECRET) because the
Actions cache is not a safe place for plain secrets."""
import base64
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
from urllib.parse import parse_qs, quote, urlparse

import requests
from cryptography.fernet import Fernet

import config as C

log = logging.getLogger("auth")


def _fernet():
    raw = hashlib.sha256(("upstox-alert-bot:" + C.UPSTOX_SECRET).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


class TokenStore:
    def __init__(self, path, fallback=""):
        self.path, self.version, self.token = path, 0, fallback
        try:
            with open(path, "rb") as f:
                self.token = json.loads(_fernet().decrypt(f.read())).get("token") or fallback
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Saved token unreadable, ignoring: %s", e)

    def get(self):
        return self.token

    def set(self, token):
        self.token = token
        self.version += 1
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            blob = json.dumps({"token": token, "saved_at": dt.datetime.utcnow().isoformat()})
            with open(self.path, "wb") as f:
                f.write(_fernet().encrypt(blob.encode()))
        except Exception as e:
            log.warning("Could not save token: %s", e)


def login_url(bot_username=""):
    # state carries the bot name so the helper page can hand the code straight to Telegram
    state = f"{bot_username}.{secrets.token_hex(4)}" if bot_username else secrets.token_hex(4)
    return ("https://api.upstox.com/v2/login/authorization/dialog?response_type=code"
            f"&client_id={quote(C.UPSTOX_API_KEY)}"
            f"&redirect_uri={quote(C.REDIRECT_URI, safe='')}&state={state}")


def extract_code(text):
    """Accepts the bare code or the full redirected URL."""
    text = (text or "").strip()
    if "code=" in text:
        return (parse_qs(urlparse(text).query).get("code") or [""])[0]
    return text if re.fullmatch(r"[A-Za-z0-9_\-]{4,64}", text) else ""


def exchange(code):
    """Returns the access token, or raises RuntimeError with Upstox's message."""
    r = requests.post("https://api.upstox.com/v2/login/authorization/token",
                      headers={"accept": "application/json",
                               "Content-Type": "application/x-www-form-urlencoded"},
                      data={"code": code, "client_id": C.UPSTOX_API_KEY,
                            "client_secret": C.UPSTOX_SECRET, "redirect_uri": C.REDIRECT_URI,
                            "grant_type": "authorization_code"}, timeout=15)
    tok = r.json().get("access_token") if r.ok else None
    if not tok:
        raise RuntimeError(r.text[:200])
    return tok
