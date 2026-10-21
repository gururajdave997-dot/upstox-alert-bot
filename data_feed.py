"""Upstox API v2 REST: candles, daily history, option chain, LTP."""
import datetime as dt
import logging
from urllib.parse import quote

import pandas as pd
import requests

log = logging.getLogger(__name__)
BASE = "https://api.upstox.com"


class TokenExpired(Exception):
    pass


class UpstoxClient:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self._cache = {}

    def _get(self, path, params=None):
        r = self.s.get(BASE + path, params=params, timeout=15)
        if r.status_code == 401:
            raise TokenExpired(r.text[:200])
        r.raise_for_status()
        j = r.json()
        if j.get("status") != "success":
            raise RuntimeError(str(j)[:300])
        return j.get("data")

    @staticmethod
    def _to_df(candles):
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume", "oi"])
        df["ts"] = pd.to_datetime(df["ts"])
        return df.drop(columns="oi").set_index("ts").sort_index()

    def _hist(self, key, interval, to_d, from_d):
        k = ("h", key, interval, to_d)
        if k not in self._cache:
            try:
                data = self._get(f"/v2/historical-candle/{quote(key, safe='')}/{interval}/"
                                 f"{to_d.isoformat()}/{from_d.isoformat()}")
                self._cache[k] = self._to_df(data.get("candles", []))
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("History %s %s failed: %s", key, interval, e)
                return self._to_df([])
        return self._cache[k]

    def candles(self, key, minutes, now):
        """Closed N-minute candles: last ~7 days + today (for indicator warm-up)."""
        today = now.date()
        prev = self._hist(key, "1minute", today - dt.timedelta(days=1), today - dt.timedelta(days=7))
        data = self._get(f"/v2/historical-candle/intraday/{quote(key, safe='')}/1minute")
        df = pd.concat([prev, self._to_df(data.get("candles", []))])
        if df.empty:
            return df
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if minutes > 1:
            df = df.resample(f"{minutes}min", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum"}).dropna(subset=["close"])
        if len(df) and df.index[-1] + pd.Timedelta(minutes=minutes) > pd.Timestamp(now):
            df = df.iloc[:-1]  # drop the still-forming candle
        return df

    def daily(self, key, today):
        """Completed daily candles up to yesterday (~400 days)."""
        return self._hist(key, "day", today - dt.timedelta(days=1), today - dt.timedelta(days=400))

    def nearest_expiry(self, key, today):
        k = ("exp", key, today)
        if k not in self._cache:
            data = self._get("/v2/option/contract", {"instrument_key": key}) or []
            exps = sorted({c["expiry"] for c in data if c.get("expiry", "") >= today.isoformat()})
            if not exps:
                raise RuntimeError(f"No expiries found for {key}")
            self._cache[k] = exps[0]
        return self._cache[k]

    def option_chain(self, key, expiry):
        return self._get("/v2/option/chain", {"instrument_key": key, "expiry_date": expiry}) or []

    def ltp(self, keys):
        out = {}
        keys = list(keys)
        for i in range(0, len(keys), 400):
            data = self._get("/v2/market-quote/ltp",
                             {"instrument_key": ",".join(keys[i:i + 400])}) or {}
            for v in data.values():
                if v.get("instrument_token") and v.get("last_price") is not None:
                    out[v["instrument_token"]] = float(v["last_price"])
        return out
