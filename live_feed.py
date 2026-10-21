"""Upstox V3 WebSocket feed -> live 1-minute candles (for scalping) + LTP cache."""
import logging
import threading
import time

import pandas as pd

log = logging.getLogger(__name__)
COLS = ["open", "high", "low", "close", "volume"]


class LiveFeed:
    def __init__(self, token, keys):
        self.token, self.keys = token, list(keys)
        self.lock = threading.Lock()
        self.streamer = None
        self.last_msg = 0.0
        self.cur, self.closed, self.seeded, self.prices, self.vtt = {}, {}, {}, {}, {}

    def start(self):
        try:
            import upstox_client
            cfg = upstox_client.Configuration()
            cfg.access_token = self.token
            self.streamer = upstox_client.MarketDataStreamerV3(
                upstox_client.ApiClient(cfg), self.keys, "full")
            self.streamer.on("message", self._on_msg)
            self.streamer.on("error", lambda e: log.warning("Feed error: %s", e))
            self.streamer.on("close", lambda *a: log.info("Feed closed"))
            self.streamer.auto_reconnect(True, 5, 20)
            self.streamer.connect()
            return True
        except Exception as e:
            log.warning("Live feed unavailable, using REST fallback: %s", e)
            return False

    def stop(self):
        try:
            if self.streamer:
                self.streamer.disconnect()
        except Exception:
            pass

    def is_live(self):
        return time.time() - self.last_msg < 90

    def seed(self, key, df):
        with self.lock:
            self.seeded[key] = df[COLS].copy() if len(df) else pd.DataFrame(columns=COLS)

    def _on_msg(self, msg):
        try:
            self.last_msg = time.time()
            for key, f in (msg.get("feeds") or {}).items():
                ff = f.get("fullFeed") or {}
                node = ff.get("marketFF") or ff.get("indexFF") or {}
                ltpc = node.get("ltpc") or f.get("ltpc") or {}
                if ltpc.get("ltp") is None:
                    continue
                ts = int(ltpc.get("ltt") or msg.get("currentTs") or time.time() * 1000) / 1000
                vol = 0
                if node.get("vtt") is not None:
                    v = int(node["vtt"])
                    prev = self.vtt.get(key)
                    vol = max(0, v - prev) if prev is not None else 0
                    self.vtt[key] = v
                self._update(key, float(ltpc["ltp"]), ts, vol)
        except Exception as e:
            log.debug("Bad feed message: %s", e)

    def _update(self, key, price, ts, vol):
        minute = int(ts // 60 * 60)
        with self.lock:
            self.prices[key] = (price, time.time())
            c = self.cur.get(key)
            if c is None or minute > c["t"]:
                if c is not None:
                    lst = self.closed.setdefault(key, [])
                    lst.append(c)
                    del lst[:-600]
                self.cur[key] = {"t": minute, "open": price, "high": price, "low": price,
                                 "close": price, "volume": vol}
            elif minute == c["t"]:
                c["high"], c["low"] = max(c["high"], price), min(c["low"], price)
                c["close"] = price
                c["volume"] += vol

    def ltp(self, key, max_age=15):
        p = self.prices.get(key)
        return p[0] if p and time.time() - p[1] <= max_age else None

    def candles(self, key):
        """Seeded REST history + closed live 1-min candles (IST index)."""
        with self.lock:
            live = list(self.closed.get(key, []))
            seed = self.seeded.get(key, pd.DataFrame(columns=COLS))
        if live:
            ldf = pd.DataFrame(live)
            ldf.index = pd.DatetimeIndex(pd.to_datetime(ldf.pop("t"), unit="s", utc=True)).tz_convert(
                "Asia/Kolkata")
            if len(seed):
                seed = seed[seed.index < ldf.index[0]]
            return pd.concat([seed, ldf[COLS]]) if len(seed) else ldf[COLS]
        return seed
