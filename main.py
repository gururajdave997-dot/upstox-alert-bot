"""Orchestrator: swing scan (pre-market), intraday 5m, scalping 1m (live feed),
options buy/sell setups, exit/trailing alerts, Telegram commands, paper trading.
Runs as a GitHub Actions job: exits at end of day, or after MAX_RUNTIME_MIN so the
workflow can start a fresh run (jobs are capped at 6 hours)."""
import datetime as dt
import html
import json
import logging
import os
import sys
import time

import pytz

import config as C
import auth
import notifier
import strategy as ST
import strike_selector as S
from data_feed import TokenExpired, UpstoxClient
from live_feed import LiveFeed
from positions import Tracker
from paper_trade import PaperBook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
IST = pytz.timezone("Asia/Kolkata")
T = dt.time.fromisoformat
PAPER_CHECK_SEC = 10  # how often paper trades are priced (keeps API calls low)


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
        self.store = auth.TokenStore(os.path.join(C.STATE_DIR, "token.bin"), C.ACCESS_TOKEN)
        self.bot_user = notifier.bot_username()
        self.session_path = os.path.join(C.STATE_DIR, "session.json")
        self.started = time.time()
        self.resumed = False
        self.client = UpstoxClient(self.store.get())
        self.token_version = self.store.version
        self.token_bad = not self.store.get()
        self.tracker = Tracker(os.path.join(C.STATE_DIR, "positions.json"))
        self.paper = PaperBook(os.path.join(C.STATE_DIR, "paper_trades.json"),
                               T(C.SQUARE_OFF_TIME))
        self.last_paper = 0.0
        self.stocks = C.parse_watchlist(C.STOCK_WATCHLIST)
        self.indices = C.parse_watchlist(C.INDEX_WATCHLIST)
        self.swings = C.parse_watchlist(C.SWING_WATCHLIST)
        self.index_keys = {k for k, _ in self.indices}
        self.live = None
        self.active = False
        self.last_scan = 0.0
        self.last_opt_scan = 0.0
        self.tg_offset = None
        self.day = None
        self.idx_bias = {}   # index name -> +1/-1/0 from 5-min trend
        self.swing_seen_path = os.path.join(C.STATE_DIR, "swing_seen.json")

    def reset_day(self, d):
        self.day, self.sent, self.done = d, set(), set()
        self.opt = {"buy": {}, "sell": {}, "iv": {}}
        self.chains, self.scalp_seen = {}, {}
        self.tracker.drop_stale_intraday(d)
        try:  # carry today's alert memory over from the previous run (no duplicate alerts)
            with open(self.session_path) as f:
                ss = json.load(f)
            if ss.get("day") == str(d):
                self.sent = set(ss["sent"])
                self.done = set(ss["done"]) - {"live", "tokcheck"}
                self.opt = ss["opt"]
                self.resumed = True
        except Exception:
            pass

    def save_session(self):
        try:
            os.makedirs(C.STATE_DIR, exist_ok=True)
            with open(self.session_path, "w") as f:
                json.dump({"day": str(self.day), "sent": sorted(self.sent),
                           "done": sorted(self.done), "opt": self.opt}, f)
        except Exception as e:
            log.warning("Could not save session: %s", e)

    # ---------- helpers ----------
    def emit(self, sig, now, ikey=None):
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
        self.paper_open(sig, ikey, now)

    # ---------- paper trading (virtual money only, no real orders) ----------
    def paper_open(self, sig, ikey, now):
        if not self.paper.enabled:
            return
        try:
            k = sig["kind"]
            if k == "OPT_BUY":
                p = sig.get("pick") or {}
                ikey = p.get("instrument_key") or p.get("key")
                name = f"{sig['symbol']} {p.get('strike')} {p.get('opt')}"
                side, lot, ltp = "BUY", int(p.get("lot_size") or p.get("lot") or 1), sig["entry"]
            elif k in ("SWING", "INTRADAY") or (k == "SCALP" and ikey not in self.index_keys):
                name, lot = sig["symbol"], 1
                side = "BUY" if sig["direction"] > 0 else "SELL"
                live_px = self.live.ltp(ikey) if self.live and k != "SWING" else None
                ltp = None if k == "SWING" else (live_px or sig["entry"])  # swing: next open
            else:
                return  # index scalps and hedged option-sell spreads are not paper traded
            if not ikey:
                log.warning("Paper: no instrument key for %s, skipped", sig["key"])
                return
            msg = self.paper.open_trade(k, name, ikey, side, sig["sl"], sig["target"],
                                        ltp=ltp, lot_size=lot, is_option=(k == "OPT_BUY"), now=now)
            if msg:
                notifier.send(msg)
        except Exception as e:
            log.warning("Paper open %s: %s", sig.get("key"), e)

    def paper_check(self, now):
        if time.time() - self.last_paper < PAPER_CHECK_SEC:
            return
        self.last_paper = time.time()
        keys = self.paper.keys_needed()
        if not keys:
            return
        px = {}
        if self.live:
            px = {k: v for k in keys if (v := self.live.ltp(k)) is not None}
        missing = [k for k in keys if k not in px]
        if missing:
            px.update(self.client.ltp(missing))
        for m in self.paper.update(px, now):
            notifier.send(m)

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
            text = m.get("text") or ""
            parts = text.split()
            if parts and "code=" in text and not parts[0].startswith("/"):
                parts = ["/code", text]  # pasted the redirected URL directly
            if not parts or not parts[0].startswith("/"):
                continue
            cmd = parts[0].split("@")[0].lower()
            if cmd in ("/start", "/code") and len(parts) > 1:
                self.use_code(parts[1])
            elif cmd == "/positions":
                notifier.send(self.tracker.summary())
            elif cmd == "/close" and len(parts) > 1:
                notifier.send(self.tracker.close(parts[1].lstrip("#")))
            elif cmd == "/closeall":
                kind = parts[1].upper() if len(parts) > 1 else None
                ids = [pid for pid, p in self.tracker.pos.items() if not kind or p["kind"] == kind]
                for pid in ids:
                    self.tracker.pos.pop(pid)
                self.tracker._save()
                notifier.send(f"✖️ Stopped tracking {len(ids)} position(s)")
            elif cmd == "/paper":
                notifier.send(self.paper.positions_text())
            elif cmd == "/pnl":
                notifier.send(self.paper.pnl_text())
            elif cmd == "/paperclose" and len(parts) > 1:
                notifier.send(self.paper.remove(parts[1].lstrip("#")))
            elif cmd == "/paperreset":
                notifier.send(self.paper.reset())
            elif cmd == "/status":
                feed = "live" if self.live and self.live.is_live() else "REST/idle"
                notifier.send(f"Active: {self.active} · Feed: {feed} · "
                              f"Tracked: {len(self.tracker.pos)} · "
                              f"Paper: {'ON' if self.paper.enabled else 'OFF'}")
            elif cmd == "/login":
                self.send_login("Login link")
            elif cmd == "/help":
                notifier.send("/login – Upstox login link\n/code XXXX – finish login manually\n/positions – tracked trades\n"
                              "/close N – stop tracking #N\n/closeall [SWING] – stop tracking all\n/status\n"
                              "/paper – open paper trades\n/pnl – paper P&L\n"
                              "/paperclose N – remove paper trade #N\n/paperreset – restart paper account")
        if first and self.tg_offset is None:
            self.tg_offset = 0

    def send_login(self, reason):
        notifier.send(f"🔑 <b>{reason}</b>\n<a href=\"{auth.login_url(self.bot_user)}\">Tap here to log in "
                      f"to Upstox</a>.\nAfter login, tap <b>Start</b> when Telegram opens – or copy the "
                      f"page address and send it here (or <code>/code XXXX</code>).")

    def use_code(self, raw):
        code = auth.extract_code(raw)
        if not code:
            return notifier.send("❌ Couldn't find a login code in that. Send /login to try again.")
        try:
            self.store.set(auth.exchange(code))
            notifier.send("✅ Upstox login successful. Scanning with today's token.")
        except Exception as e:
            notifier.send(f"❌ Login failed: {html.escape(str(e))}\nSend /login for a fresh link "
                          f"(codes work once and expire quickly).")

    def token_refresh(self):
        """Pick up a new token saved by the login page."""
        if self.store.version != self.token_version:
            self.token_version = self.store.version
            self.client.set_token(self.store.get())
            self.token_bad = False
            if self.live:
                self.live.stop()
                self.live = None
            self.done.discard("live")
            self.done.discard("tokcheck")

    # ---------- scans ----------
    def _swing_seen(self):
        try:
            with open(self.swing_seen_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def swing_scan(self, now, eod=False):
        n = 0
        regime = 0
        mk = next((k for k, nm in self.indices if nm == C.MARKET_INDEX), None)
        if mk:
            try:
                regime = ST.daily_regime(self.client.daily(mk, now.date(), include_today=eod))
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Regime: %s", e)
        seen = self._swing_seen()  # same daily candle is seen by 15:40 scan and next 8:30 scan
        cutoff = (now.date() - dt.timedelta(days=7)).isoformat()
        seen = {k: v for k, v in seen.items() if v >= cutoff}
        for key, name in self.swings:
            try:
                sig = ST.swing_signal(self.client.daily(key, now.date(), include_today=eod),
                                      name, key, regime)
                if sig and sig["key"] not in seen:
                    seen[sig["key"]] = now.date().isoformat()
                    n += 1
                    self.emit(sig, now, key)
                time.sleep(0.2)  # stay well inside Upstox API rate limits on big lists
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Swing %s: %s", name, e)
        try:
            with open(self.swing_seen_path, "w") as f:
                json.dump(seen, f)
        except Exception:
            pass
        when = "Post-close" if eod else "Pre-market"
        reg = {1: "NIFTY above 50 DMA", -1: "NIFTY below 50 DMA"}.get(regime, "regime n/a")
        notifier.send(f"📅 {when} swing scan done: {n} setup(s) from {len(self.swings)} stocks "
                      f"({reg}).")

    def scalp_eval(self, key, name, df, now):
        if df is None or not len(df) or self.scalp_seen.get(key) == df.index[-1]:
            return
        self.scalp_seen[key] = df.index[-1]
        is_idx = key in self.index_keys
        sig = ST.scalp_signal(df, name, key, is_idx, self.idx_bias.get(C.MARKET_INDEX, 0))
        if sig and is_idx and name in self.chains:
            cdf, ctx = self.chains[name]
            sig["pick"] = S.best_buy(cdf, "CE" if sig["direction"] > 0 else "PE", ctx["spot"])
        self.emit(sig, now, key)

    def scan(self, now):
        entries = between(now, C.ENTRY_START, C.NO_NEW_ENTRY_AFTER)
        rest_scalp = C.ENABLE_SCALP and not (self.live and self.live.is_live())
        for key, name in self.indices:  # index trend = filter for stocks and option buys
            try:
                self.idx_bias[name] = ST.trend_bias(self.client.candles(key, C.CANDLE_MINUTES, now))
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Bias %s: %s", name, e)
        mkt = self.idx_bias.get(C.MARKET_INDEX, 0)
        for key, name in self.stocks:
            try:
                if C.ENABLE_INTRADAY and entries:
                    self.emit(ST.intraday_signal(self.client.candles(key, C.CANDLE_MINUTES, now),
                                                 name, key, mkt), now, key)
                if rest_scalp and entries:
                    self.scalp_eval(key, name, self.client.candles(key, 1, now), now)
            except TokenExpired:
                raise
            except Exception as e:
                log.warning("Stock scan %s: %s", name, e)
        if time.time() - self.last_opt_scan < C.OPTIONS_SCAN_SEC:
            return
        self.last_opt_scan = time.time()
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
                late_expiry = expiry == now.date().isoformat() and \
                    now.time() >= T(C.OPT_EXPIRY_CUTOFF)
                if C.ENABLE_OPTIONS_BUY and entries and not late_expiry \
                        and now.time() >= T(C.OPT_BUY_START):
                    prev = self.opt["buy"].get(name)
                    if ctx["bias"] != prev:
                        sig = ST.options_buy_setup(cdf, ctx, self.idx_bias.get(name, 0))
                        if sig or ctx["bias"] == "NEUTRAL":
                            self.opt["buy"][name] = ctx["bias"]  # else retry until price agrees
                        if sig:
                            sig["pick"]["lot_size"] = self.client.lots.get(sig["pick"]["key"], 1)
                        self.emit(sig, now)
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
        self.live = LiveFeed(self.store.get(), keys)
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
        if C.ENABLE_SWING and C.SWING_EOD_TIME and "swing_eod" not in self.done \
                and now.time() >= T(C.SWING_EOD_TIME):
            self.done.add("swing_eod")
            self.swing_scan(now, eod=True)
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
        if self.paper.enabled:
            self.paper_check(now)
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
        """Returns True if the workflow should start another run to continue today."""
        now = dt.datetime.now(IST)
        if not C.is_market_day(now.date()) or now.time() >= T(C.ACTIVE_END):
            log.info("Market holiday/weekend or outside 08:00–17:00 window – nothing to do.")
            return False
        self.reset_day(now.date())
        notifier.send("🔁 Bot resumed in a fresh GitHub run." if self.resumed
                      else "🤖 Upstox alert bot started. Send /help for commands.")
        if self.token_bad:
            self.send_login("Upstox login needed to start")
        try:
            return self.loop()
        finally:
            if self.live:
                self.live.stop()
            self.save_session()
            if self.paper.enabled:
                self.paper.save()

    def loop(self):
        while True:
            now = dt.datetime.now(IST)
            if now.time() >= T(C.ACTIVE_END):
                if self.paper.enabled:
                    notifier.send(self.paper.pnl_text(now))
                notifier.send("😴 Scan window closed for today.")
                return False
            if time.time() - self.started >= C.MAX_RUNTIME_MIN * 60:
                log.info("Runtime limit reached – handing over to a new run.")
                return True
            self.handle_commands()
            self.token_refresh()
            if now.time() < T(C.ACTIVE_START):
                time.sleep(30)
                continue
            if not self.active:
                if not self.resumed:
                    notifier.send("✅ Scanner active. Watching for setups…")
                self.active = True
            try:
                if "tokcheck" not in self.done and not self.token_bad:
                    self.done.add("tokcheck")
                    self.client.check_token()
                if self.token_bad:
                    time.sleep(C.FAST_LOOP_SEC)
                    continue
                self.tick(now)
            except TokenExpired:
                self.token_bad = True
                self.send_login("Upstox token expired – please log in")
                time.sleep(C.FAST_LOOP_SEC)
                continue
            except Exception as e:
                log.exception("Tick error: %s", e)
            time.sleep(C.FAST_LOOP_SEC)


if __name__ == "__main__":
    miss = C.missing_vars()
    if miss:
        log.error("Missing environment variables: %s", ", ".join(miss))
        sys.exit(1)
    cont = Bot().run()
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"continue={'true' if cont else 'false'}\n")
