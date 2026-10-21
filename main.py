"""Orchestrator: swing scan (pre-market), intraday 5m, scalping 1m (live feed),
options buy/sell setups, exit/trailing alerts, Telegram commands."""
import datetime as dt
import html
import logging
import os
import sys
import time

import pytz

import config as C
import notifier
import strategy as ST
import strike_selector as S
from data_feed import TokenExpired, UpstoxClient
from live_feed import LiveFeed
from positions import Tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
IST = pytz.timezone("Asia/Kolkata")
T = dt.time.fromisoformat


def between(now, a, b):
    return T(a) <= now.time() < T(b)


def arrow(d):
    return "🟢 BUY" if d > 0 else "🔴 SELL"


def fmt(sig, pid):
    s = html.escape(sig["symbol"])
    k = sig["kind"]
    track = f"\n<i>Tracking #{pid} · /close {pid} if not taken</i>" if pid else ""
    if k == "INTRADAY":
        return (f"{arrow(sig['direction'])} <b>INTRADAY {s}</b>\n"
                f"Entry ~{sig['entry']} | SL {sig['sl']} | Target {sig['target']} (1:{C.INTRADAY_RR:g})\n"
                f"EMA{C.EMA_FAST}/{C.EMA_SLOW} cross · Vol {sig['vol_ratio']}x · VWAP ok · "
                f"{sig['candle']} ({C.CANDLE_MINUTES}m){track}")
    if k == "SCALP":
        opt = ""
        if sig.get("pick"):
            p = sig["pick"]
            opt = (f"\nOption: <b>Buy {s} {p['strike']} {p['opt']}</b> ~{p['ltp']} "
                   f"({html.escape(p['why'])})")
        return (f"⚡{arrow(sig['direction'])} <b>SCALP {s}</b>\n"
                f"Entry ~{sig['entry']} | SL {sig['sl']} | Target {sig['target']} (ATR)\n"
                f"EMA{C.SCALP_EMA_FAST}/{C.SCALP_EMA_SLOW} cross · {sig['note']} · "
                f"{sig['candle']} (1m){opt}{track}")
    if k == "SWING":
        what = "breakout above" if sig["direction"] > 0 else "breakdown below"
        return (f"📅{arrow(sig['direction'])} <b>SWING {s}</b>\n"
                f"Close {sig['entry']} – {C.SWING_LOOKBACK}-day {what} {sig['level']}\n"
                f"SL {sig['sl']} ({C.SWING_ATR_SL:g}×ATR) | Target {sig['target']} (1:{C.SWING_RR:g})\n"
                f"Vol {sig['vol_ratio']}x · {sig['note']}{track}")
    ctx = (f"Spot {sig['spot']} · PCR {sig['pcr']} · Support {sig['support']} / "
           f"Resistance {sig['resistance']} · ATM IV {sig['atm_iv']} · Exp {sig['expiry']}")
    if k == "OPT_BUY":
        p = sig["pick"]
        icon = "📈" if sig["direction"] > 0 else "📉"
        return (f"{icon} <b>{sig['bias']} {s} → BUY {p['strike']} {p['opt']}</b>\n"
                f"Premium ~{sig['entry']} | SL {sig['sl']} | Target {sig['target']}\n"
                f"Best strike: {html.escape(p['why'])}\n"
                f"ΔOI CE {sig['ce_chg']:,} · PE {sig['pe_chg']:,}\n{ctx}{track}")
    if k == "OPT_SELL":
        legs = "\n".join(f"{'SELL' if l['act'] == 'SELL' else 'BUY '} {l['strike']} {l['opt']} @ {l['ltp']}"
                         + (f" (Δ {l['delta']})" if l["delta"] is not None else "")
                         for l in sig["legs"])
        return (f"🛡️ <b>{sig['strategy']} – {s}</b> ({sig['bias'].lower()})\n{legs}\n"
                f"Net credit {sig['entry']} pts · Max loss {sig['max_loss']} pts · "
                f"BE {', '.join(map(str, sig['breakevens']))}\n"
                f"Exit if spread ≥ {sig['sl']} · Book at ≤ {sig['target']}\n"
                f"{ctx}\n<i>Hedged; needs margin.</i>{track}")
    if k == "IV":
        return (f"⚡ <b>IV spike – {s}</b>: ATM IV {sig['prev_iv']} → {sig['atm_iv']}\n{ctx}")
    return str(sig)


class Bot:
    def __init__(self):
        self.client = UpstoxClient(C.ACCESS_TOKEN)
        self.tracker = Tracker(os.path.join(C.STATE_DIR, "positions.json"))
        self.stocks = C.parse_watchlist(C.STOCK_WATCHLIST)
        self.indices = C.parse_watchlist(C.INDEX_WATCHLIST)
        self.swings = C.parse_watchlist(C.SWING_WATCHLIST)
        self.index_keys = {k for k, _ in self.indices}
        self.live = None
        self.active = False
        self.token_alerted = False
        self.last_scan = 0.0
        self.tg_offset = None
        self.day = None

    def reset_day(self, d):
        self.day, self.sent, self.done = d, set(), set()
        self.opt = {"buy": {}, "sell": {}, "iv": {}}
        self.chains, self.scalp_seen = {}, {}
        self.tracker.drop_stale_intraday(d)

    # ---------- helpers ----------
    def emit(self, sig, now):
        if not sig or sig["key"] in self.sent:
            return
        self.sent.add(sig["key"])
        pid, msgs = (self.tracker.open(sig, now) if C.ENABLE_EXIT_ALERTS else (None, []))
        if msgs == ["DUP"]:
            return
        for m in msgs:
            notifier.send(m)
        log.info("SIGNAL %s", sig["key"])
        notifier.send(fmt(sig, pid))

    def handle_commands(self):
        ups = notifier.get_updates(self.tg_offset)
        first = self.tg_offset is None
        for u in ups:
            self.tg_offset = u["update_id"] + 1
            if first:
                continue  # ignore backlog from before startup
            m = u.get("message") or u.get("channel_post") or {}
            if str(m.get("chat", {}).get("id")) != C.TELEGRAM_CHAT_ID:
                continue
            parts = (m.get("text") or "").split()
            if not parts or not parts[0].startswith("/"):
                continue
            cmd = parts[0].split("@")[0].lower()
            if cmd == "/positions":
                notifier.send(self.tracker.summary())
            elif cmd == "/close" and len(parts) > 1:
                notifier.send(self.tracker.close(parts[1].lstrip("#")))
            elif cmd == "/status":
                feed = "live" if self.live and self.live.is_live() else "REST/idle"
                notifier.send(f"Active: {self.active} · Feed: {feed} · "
                              f"Tracked: {len(self.tracker.pos)}")
            elif cmd == "/help":
                notifier.send("/positions – tracked trades\n/close N – stop tracking #N\n/status")
        if first and self.tg_offset is None:
            self.tg_offset = 0

    # ---------- scans ----------
    def swing_scan(self, now):
        n = 0
        for key, name in self.swings:
            try:
                sig = ST.swing_signal(self.client.daily(key, now.date()), name, key)
                if sig:
                    n += 1
                    self.emit(sig, now)
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Swing %s: %s", name, e)
        notifier.send(f"📅 Swing scan done: {n} setup(s) from {len(self.swings)} stocks.")

    def scalp_eval(self, key, name, df, now):
        if df is None or not len(df) or self.scalp_seen.get(key) == df.index[-1]:
            return
        self.scalp_seen[key] = df.index[-1]
        is_idx = key in self.index_keys
        sig = ST.scalp_signal(df, name, key, is_idx)
        if sig and is_idx and name in self.chains:
            cdf, ctx = self.chains[name]
            sig["pick"] = S.best_buy(cdf, "CE" if sig["direction"] > 0 else "PE", ctx["spot"])
        self.emit(sig, now)

    def scan(self, now):
        entries = between(now, C.ENTRY_START, C.NO_NEW_ENTRY_AFTER)
        rest_scalp = C.ENABLE_SCALP and not (self.live and self.live.is_live())
        for key, name in self.stocks:
            try:
                if C.ENABLE_INTRADAY and entries:
                    self.emit(ST.intraday_signal(self.client.candles(key, C.CANDLE_MINUTES, now),
                                                 name, key), now)
                if rest_scalp and entries:
                    self.scalp_eval(key, name, self.client.candles(key, 1, now), now)
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Stock scan %s: %s", name, e)
        for key, name in self.indices:
            try:
                expiry = self.client.nearest_expiry(key, now.date())
                cdf = S.chain_df(self.client.option_chain(key, expiry))
                if cdf.empty:
                    continue
                ctx = ST.analyse_chain(cdf, name, expiry, key)
                self.chains[name] = (cdf, ctx)
                if rest_scalp and entries:
                    self.scalp_eval(key, name, self.client.candles(key, 1, now), now)
                if C.ENABLE_OPTIONS_BUY and entries:
                    prev = self.opt["buy"].get(name)
                    self.opt["buy"][name] = ctx["bias"]
                    if ctx["bias"] != prev:
                        self.emit(ST.options_buy_setup(cdf, ctx), now)
                if C.ENABLE_OPTIONS_SELL and between(now, C.SELL_START, C.SELL_END):
                    prev = self.opt["sell"].get(name)
                    self.opt["sell"][name] = ctx["bias"]
                    if ctx["bias"] != prev:
                        self.emit(ST.options_sell_setup(cdf, ctx), now)
                spike = ST.iv_spike(ctx, self.opt["iv"])
                if spike:
                    notifier.send(fmt({**spike, "kind": "IV"}, None))
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Options scan %s: %s", name, e)

    def start_live(self, now):
        keys = [k for k, _ in self.stocks + self.indices]
        self.live = LiveFeed(C.ACCESS_TOKEN, keys)
        for key, _ in self.stocks + self.indices:
            try:
                self.live.seed(key, self.client.candles(key, 1, now))
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Seed %s: %s", key, e)
        if not self.live.start():
            self.live = None

    def check_positions(self):
        keys = self.tracker.keys_needed()
        if not keys:
            return
        px = {}
        if self.live:
            px = {k: v for k in keys if (v := self.live.ltp(k)) is not None}
        missing = [k for k in keys if k not in px]
        if missing:
            px.update(self.client.ltp(missing))
        for m in self.tracker.check(px):
            notifier.send(m)

    def tick(self, now):
        if C.ENABLE_SWING and "swing" not in self.done and now.time() >= T(C.SWING_SCAN_TIME):
            self.done.add("swing")
            self.swing_scan(now)
        if not between(now, C.MARKET_OPEN, C.MARKET_CLOSE):
            if self.live:
                self.live.stop()
                self.live = None
            return
        if C.ENABLE_SCALP and "live" not in self.done:
            self.done.add("live")
            self.start_live(now)
        if C.ENABLE_EXIT_ALERTS:
            self.check_positions()
        if "sq" not in self.done and now.time() >= T(C.SQUARE_OFF_TIME):
            self.done.add("sq")
            for m in self.tracker.square_off():
                notifier.send(m)
        if C.ENABLE_SCALP and self.live and self.live.is_live() \
                and between(now, C.ENTRY_START, C.NO_NEW_ENTRY_AFTER):
            for key, name in self.stocks + self.indices:
                self.scalp_eval(key, name, self.live.candles(key), now)
        if time.time() - self.last_scan >= C.SCAN_INTERVAL_SEC:
            self.last_scan = time.time()
            self.scan(now)

    def run(self):
        notifier.send("🤖 Upstox alert bot started. Send /help for commands.")
        while True:
            now = dt.datetime.now(IST)
            if now.date() != self.day:
                self.reset_day(now.date())
            self.handle_commands()
            in_window = now.weekday() < 5 and between(now, C.ACTIVE_START, C.ACTIVE_END)
            if not in_window:
                if self.active:
                    notifier.send("😴 Scan window closed for today.")
                    self.active = False
                    if self.live:
                        self.live.stop()
                        self.live = None
                time.sleep(30)
                continue
            if not self.active:
                notifier.send("✅ Scanner active. Watching for setups…")
                self.active = True
            try:
                self.tick(now)
            except TokenExpired:
                if not self.token_alerted:
                    notifier.send("⚠️ <b>Upstox ACCESS_TOKEN expired/invalid.</b>\n"
                                  "Run get_token.py and update ACCESS_TOKEN in Railway Variables.")
                    self.token_alerted = True
                time.sleep(300)
                continue
            except Exception as e:
                log.exception("Tick error: %s", e)
            time.sleep(C.FAST_LOOP_SEC)


if __name__ == "__main__":
    miss = C.missing_vars()
    if miss:
        log.error("Missing environment variables: %s", ", ".join(miss))
        sys.exit(1)
    Bot().run()
