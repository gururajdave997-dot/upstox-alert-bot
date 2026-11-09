"""Upstox V3 WebSocket feed -> live LTP cache (faster exit alerts and paper fills)."""
import logging
import threading
import time

log = logging.getLogger(__name__)


class LiveFeed:
    def __init__(self, token, keys):
        self.token, self.keys = token, list(keys)
        self.lock = threading.Lock()
        self.streamer = None
        self.last_msg = 0.0
        self.prices = {}

    def start(self):
        try:
            import upstox_client
            cfg = upstox_client.Configuration()
            cfg.access_token = self.token
            self.streamer = upstox_client.MarketDataStreamerV3(
                upstox_client.ApiClient(cfg), self.keys, "ltpc")
            self.streamer.on("message", self._on_msg)
            self.streamer.on("error", lambda e: log.warning("Feed error: %s", e))
            self.streamer.on("close", lambda *a: log.info("Feed closed"))
            self.streamer.auto_reconnect(True, 5, 20)
            self.streamer.connect()
            return True
        except Exception as e:
            log.warning("Live feed unavailable, using REST prices: %s", e)
            return False

    def stop(self):
        try:
            if self.streamer:
                self.streamer.disconnect()
        except Exception:
            pass

    def is_live(self):
        return time.time() - self.last_msg < 90

    def _on_msg(self, msg):
        try:
            self.last_msg = time.time()
            for key, f in (msg.get("feeds") or {}).items():
                ff = f.get("fullFeed") or {}
                node = ff.get("marketFF") or ff.get("indexFF") or {}
                ltpc = node.get("ltpc") or f.get("ltpc") or {}
                if ltpc.get("ltp") is not None:
                    with self.lock:
                        self.prices[key] = (float(ltpc["ltp"]), time.time())
        except Exception as e:
            log.debug("Bad feed message: %s", e)

    def ltp(self, key, max_age=15):
        p = self.prices.get(key)
        return p[0] if p and time.time() - p[1] <= max_age else None
