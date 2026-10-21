"""Tiny web server on Railway for daily Upstox login from your phone.
/login?k=<one-time key from Telegram>  ->  Upstox login  ->  /callback saves the token."""
import datetime as dt
import html
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import requests

import config as C
import notifier

log = logging.getLogger("web")


class TokenStore:
    def __init__(self, path, fallback=""):
        self.path, self.lock, self.version = path, threading.Lock(), 0
        self.token = fallback
        try:
            with open(path) as f:
                self.token = json.load(f).get("token") or fallback
        except Exception:
            pass

    def get(self):
        return self.token

    def set(self, token):
        with self.lock:
            self.token = token
            self.version += 1
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w") as f:
                    json.dump({"token": token, "saved_at": dt.datetime.utcnow().isoformat()}, f)
            except Exception as e:
                log.warning("Could not save token: %s", e)


class Links:
    """One-time login keys (valid 12h) and OAuth states (valid 10 min)."""
    def __init__(self):
        self.keys, self.states = {}, {}

    def new_link(self):
        k = secrets.token_urlsafe(16)
        self.keys[k] = time.time() + 12 * 3600
        return f"{C.PUBLIC_URL}/login?k={k}"

    def key_ok(self, k):
        return bool(k) and self.keys.get(k, 0) > time.time()

    def new_state(self):
        s = secrets.token_urlsafe(16)
        self.states[s] = time.time() + 600
        return s

    def use_state(self, s):
        return self.states.pop(s, 0) > time.time()


STORE: TokenStore = None
LINKS: Links = None


def _page(title, msg):
    return (f"<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title></head><body style='font-family:sans-serif;padding:24px'>"
            f"<h2>{title}</h2><p>{msg}</p></body></html>").encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {"Content-Type": "text/html; charset=utf-8"}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/health"):
            return self._send(200, b"ok", {"Content-Type": "text/plain"})
        if u.path == "/login":
            if not LINKS.key_ok(q.get("k")):
                return self._send(403, _page("Link expired", "Send /login to your Telegram bot for a new link."))
            url = (f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code"
                   f"&client_id={quote(C.UPSTOX_API_KEY)}&redirect_uri={quote(C.REDIRECT_URI, safe='')}"
                   f"&state={LINKS.new_state()}")
            return self._send(302, b"", {"Location": url})
        if u.path == "/callback":
            if not LINKS.use_state(q.get("state", "")) or not q.get("code"):
                return self._send(400, _page("Login failed", "Invalid or expired request. Send /login again."))
            try:
                r = requests.post("https://api.upstox.com/v2/login/authorization/token",
                                  headers={"accept": "application/json",
                                           "Content-Type": "application/x-www-form-urlencoded"},
                                  data={"code": q["code"], "client_id": C.UPSTOX_API_KEY,
                                        "client_secret": C.UPSTOX_SECRET,
                                        "redirect_uri": C.REDIRECT_URI,
                                        "grant_type": "authorization_code"}, timeout=15)
                tok = r.json().get("access_token") if r.ok else None
            except Exception as e:
                tok, r = None, None
                log.warning("Token exchange error: %s", e)
            if not tok:
                detail = html.escape(r.text[:200]) if r is not None else "network error"
                return self._send(400, _page("Login failed", f"Upstox said: {detail}"))
            STORE.set(tok)
            LINKS.keys.clear()
            notifier.send("✅ Upstox login successful. Bot is using the new token.")
            return self._send(200, _page("✅ Done", "Token saved. You can close this page."))
        self._send(404, b"not found", {"Content-Type": "text/plain"})

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)


def start(store, links):
    global STORE, LINKS
    STORE, LINKS = store, links
    srv = ThreadingHTTPServer(("0.0.0.0", C.PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Login server on port %s, public URL %s", C.PORT, C.PUBLIC_URL)
    return srv
