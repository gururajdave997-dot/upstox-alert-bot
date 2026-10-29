"""
paper_trade.py - virtual (paper) trading ledger for the Upstox Alert Bot.
No real orders are ever placed. It only uses live prices the bot already fetches.

Settings (add to BOT_SETTINGS, one per line):
  PAPER_TRADING=ON          turn paper trading on
  PAPER_CAPITAL=500000      starting virtual capital (Rs)
  PAPER_RISK_PCT=1          % of capital risked per trade
  PAPER_SLIPPAGE_PCT=0.05       stock slippage per side, %
  PAPER_OPT_SLIPPAGE_PCT=0.5     option slippage per side, % (bid-ask is wide)
  PAPER_COST_INTRADAY_PCT=0.02   statutory charges, % of turnover (STT, exch, stamp, GST)
  PAPER_COST_DELIVERY_PCT=0.12   same for swing / delivery (STT 0.1% each side)
  PAPER_COST_OPTION_PCT=0.07     same for options, % of premium turnover
  PAPER_BROKERAGE=20             Rs per order (x2 per trade, + 18% GST)
  PAPER_MAX_NOTIONAL_PCT=20      max position value per SWING trade, % of capital
  PAPER_MAX_EXPOSURE_PCT=100     max value of all open stock trades, % of capital
  PAPER_MAX_OPEN=SWING:8,INTRADAY:3,SCALP:2,OPT_BUY:2   open trades allowed per type
  PAPER_MAX_TRADES_DAY=8         new intraday/scalp/option trades per day
  PAPER_DAILY_LOSS_PCT=1.5       stop new intraday-type trades after this day loss
  PAPER_MIN_R_COST=3             stop distance must be >= 3x the round-trip cost
  PAPER_PARTIAL=ON               book half at 1R, trail the rest
  SWING_MAX_DAYS=15              time exit for swing trades (calendar days)
  TRAIL_R=1                      trail distance in R after 1R
"""
import json, os
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
LEDGER_FILE = "paper_trades.json"
MARKET_OPEN = time(9, 15)
SQUARE_OFF = time(15, 15)


def _settings():
    s = {}
    for line in os.environ.get("BOT_SETTINGS", "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            s[k.strip().upper()] = v.strip()
    try:  # also accept values the bot's config module already parsed
        import config
        for k in ("PAPER_TRADING", "PAPER_CAPITAL", "PAPER_RISK_PCT", "PAPER_SLIPPAGE_PCT",
                  "SWING_MAX_DAYS", "TRAIL_R"):
            if k not in s and getattr(config, k, None) is not None:
                s[k] = str(getattr(config, k))
    except Exception:
        pass
    for k, v in os.environ.items():
        if (k.startswith("PAPER_") or k in ("SWING_MAX_DAYS", "TRAIL_R")) and k not in s:
            s[k] = v
    return s


class PaperBook:
    def __init__(self, path=LEDGER_FILE, square_off=SQUARE_OFF):
        cfg = _settings()
        g = lambda k, d: float(cfg.get(k, d))
        self.enabled = cfg.get("PAPER_TRADING", "OFF").upper() == "ON"
        self.start_capital = g("PAPER_CAPITAL", 500000)
        self.risk_pct = g("PAPER_RISK_PCT", 1)
        self.slip_pct = g("PAPER_SLIPPAGE_PCT", 0.05)
        self.opt_slip_pct = g("PAPER_OPT_SLIPPAGE_PCT", 0.5)
        self.cost = {"INTRADAY": g("PAPER_COST_INTRADAY_PCT", 0.02),
                     "SCALP": g("PAPER_COST_INTRADAY_PCT", 0.02),
                     "SWING": g("PAPER_COST_DELIVERY_PCT", 0.12),
                     "OPT_BUY": g("PAPER_COST_OPTION_PCT", 0.07)}
        self.brokerage = g("PAPER_BROKERAGE", 20) * 2 * 1.18
        self.max_notional_pct = g("PAPER_MAX_NOTIONAL_PCT", 20)
        self.max_exposure_pct = g("PAPER_MAX_EXPOSURE_PCT", 100)
        self.max_open = {"SWING": 8, "INTRADAY": 3, "SCALP": 2, "OPT_BUY": 2}
        for part in cfg.get("PAPER_MAX_OPEN", "").split(","):
            k, _, v = part.partition(":")
            if k.strip() and v.strip():
                self.max_open[k.strip().upper()] = int(v)
        self.max_trades_day = int(g("PAPER_MAX_TRADES_DAY", 8))
        self.daily_loss_pct = g("PAPER_DAILY_LOSS_PCT", 1.5)
        self.min_r_cost = g("PAPER_MIN_R_COST", 3)
        self.partial = cfg.get("PAPER_PARTIAL", "ON").upper() == "ON"
        self.swing_days = int(g("SWING_MAX_DAYS", 15))
        self.trail_r = g("TRAIL_R", 1)
        self.square_off = square_off
        self.path = path
        self.data = self._load()

    # ---------- storage ----------
    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                return json.load(f)
        return {"capital": self.start_capital, "next_id": 1, "open": [], "closed": []}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=1)

    def keys_needed(self):
        return list({t["ikey"] for t in self.data["open"]})

    # ---------- helpers ----------
    def _slip(self, t):
        return self.opt_slip_pct if t["is_option"] else self.slip_pct

    def _charges(self, t, entry, exit_px, qty):
        pct = self.cost.get(t["kind"], self.cost["INTRADAY"])
        return (entry + exit_px) * qty * pct / 100 + self.brokerage

    def _today_closed(self, now):
        today = now.date().isoformat()
        return [r for r in self.data["closed"] if r["closed"].startswith(today)]

    def _block_reason(self, kind, now):
        """Risk rules checked before any new trade."""
        n_open = sum(1 for t in self.data["open"] if t["kind"] == kind)
        if n_open >= self.max_open.get(kind, 99):
            return f"{n_open} {kind} trades already open"
        if kind == "SWING":
            return None
        today = self._today_closed(now)
        opened_today = [t for t in self.data["open"] + self.data["closed"]
                        if t["kind"] != "SWING" and t["alerted"][:10] == now.date().isoformat()]
        if len(opened_today) >= self.max_trades_day:
            return f"{self.max_trades_day} intraday-type trades already taken today"
        day_net = sum(r["net"] for r in today if r["kind"] != "SWING")
        if day_net <= -self.data["capital"] * self.daily_loss_pct / 100:
            return f"daily loss limit hit (Rs {day_net:,.0f})"
        return None

    # ---------- trades ----------
    def open_trade(self, kind, symbol, ikey, side, sl, target, ltp=None,
                   lot_size=1, is_option=False, now=None):
        """Call when an alert fires. ikey = Upstox instrument key whose price is tracked.
        Before 9:15 (or ltp=None) the trade waits and fills at the first live price."""
        if not self.enabled:
            return None
        now = now or datetime.now(IST)
        kind = kind.upper()
        why = self._block_reason(kind, now)
        if why:
            return f"📝 PAPER skip {symbol}: {why}"
        t = {"id": self.data["next_id"], "kind": kind, "symbol": symbol, "ikey": ikey,
             "side": side.upper(), "sl": float(sl), "target": float(target),
             "lot": max(1, int(lot_size)), "is_option": is_option, "pending": True,
             "alerted": now.isoformat(timespec="minutes")}
        self.data["next_id"] += 1
        self.data["open"].append(t)
        if ltp is not None and now.time() >= MARKET_OPEN:
            msg = self._fill(t, float(ltp), now)
        else:
            msg = f"📝 PAPER #{t['id']} queued: {t['side']} {symbol}, fills at market open"
        self.save()
        return msg

    def _fill(self, t, ltp, now):
        buy = t["side"] == "BUY"
        if (buy and (ltp <= t["sl"] or ltp >= t["target"])) or \
           (not buy and (ltp >= t["sl"] or ltp <= t["target"])):
            self.data["open"].remove(t)
            return f"📝 PAPER #{t['id']} {t['symbol']} skipped: price already past SL/target"
        slip = ltp * self._slip(t) / 100
        entry = ltp + slip if buy else ltp - slip
        risk_unit = abs(entry - t["sl"])
        # costs per unit for a round trip (slippage both sides + charges)
        pct = self.cost.get(t["kind"], self.cost["INTRADAY"])
        friction = entry * (2 * self._slip(t) + 2 * pct) / 100
        if risk_unit < self.min_r_cost * friction:
            self.data["open"].remove(t)
            return (f"📝 PAPER #{t['id']} {t['symbol']} skipped: stop {risk_unit:.2f} too tight "
                    f"vs costs {friction:.2f} per unit")
        cap = self.data["capital"]
        qty = int((cap * self.risk_pct / 100) // risk_unit) if risk_unit > 0 else 0
        if t["kind"] == "SWING":
            qty = min(qty, int(cap * self.max_notional_pct / 100 // entry))
        if not t["is_option"]:  # all open stock trades together stay within the exposure cap
            used = sum(o["entry"] * o["qty"] for o in self.data["open"]
                       if not o.get("pending") and not o["is_option"])
            room = cap * self.max_exposure_pct / 100 - used
            qty = min(qty, int(max(room, 0) // entry))
        else:
            qty = min(qty, int(cap * 0.10 // entry))  # premium outlay <= 10% of capital
        qty = (qty // t["lot"]) * t["lot"]          # whole lots for options
        if qty <= 0:
            self.data["open"].remove(t)
            return f"📝 PAPER #{t['id']} {t['symbol']} skipped: no room / quantity too small"
        t.update(pending=False, entry=round(entry, 2), qty=qty, qty0=qty, best=entry,
                 risk_unit=risk_unit, booked=0.0, opened=now.isoformat(timespec="minutes"))
        return (f"📝 PAPER #{t['id']} {t['side']} {t['symbol']} x{qty} @ {entry:.2f} "
                f"| SL {t['sl']} | Tgt {t['target']}")

    def update(self, prices, now=None):
        """Call on every check with {instrument_key: last_price}. Returns messages to send."""
        if not self.enabled:
            return []
        now = now or datetime.now(IST)
        msgs = []
        for t in list(self.data["open"]):
            ltp = prices.get(t["ikey"])
            if ltp is None:
                continue
            ltp = float(ltp)
            if t["pending"]:
                if now.time() >= MARKET_OPEN:
                    msgs.append(self._fill(t, ltp, now))
                continue
            t["ltp"] = ltp
            buy = t["side"] == "BUY"
            f = 1 if buy else -1
            r = t["risk_unit"]
            if (ltp - t.get("best", t["entry"])) * f > 0:
                t["best"] = ltp
            # at +1R: optionally book half, then SL to cost (+ charges) and trail by TRAIL_R
            if (ltp - t["entry"]) * f >= r:
                if self.partial and not t.get("half") and t["qty"] >= 2 * t["lot"]:
                    msgs.append(self._book_half(t, ltp, now))
                be = t["entry"] + f * t["entry"] * self.cost.get(t["kind"], 0.02) / 100 * 2
                trail = t["best"] - f * self.trail_r * r
                new_sl = max(be, trail) if buy else min(be, trail)
                if (new_sl - t["sl"]) * f > 0:
                    t["sl"] = round(new_sl, 2)
            reason = None
            opened_day = t["opened"][:10]
            if (ltp - t["sl"]) * f <= 0:
                reason = "trailing SL hit" if (t["sl"] - t["entry"]) * f >= 0 else "SL hit"
            elif (ltp - t["target"]) * f >= 0:
                reason = "Target hit"
            elif t["kind"] != "SWING" and (now.time() >= self.square_off
                                           or opened_day < now.date().isoformat()):
                reason = "square-off"
            elif t["kind"] == "SWING" and (now.date() - datetime.fromisoformat(
                    t["opened"]).date()).days >= self.swing_days:
                reason = f"time exit ({self.swing_days} days)"
            if reason:
                msgs.append(self._close(t, ltp, reason, now))
        if msgs:
            self.save()
        return msgs

    def _book_half(self, t, ltp, now):
        buy = t["side"] == "BUY"
        half = (t["qty"] // 2 // t["lot"]) * t["lot"]
        slip = ltp * self._slip(t) / 100
        px = ltp - slip if buy else ltp + slip
        gross = (px - t["entry"]) * half * (1 if buy else -1)
        net = gross - (self._charges(t, t["entry"], px, half) - self.brokerage / 2)
        t["qty"] -= half
        t["half"] = True
        t["booked"] = round(t.get("booked", 0) + net, 2)
        self.data["capital"] = round(self.data["capital"] + net, 2)
        return (f"💰 PAPER #{t['id']} {t['symbol']} booked half x{half} @ {px:.2f} "
                f"(+1R) | Rs {net:,.0f} | rest trails")

    def _close(self, t, ltp, reason, now):
        buy = t["side"] == "BUY"
        slip = ltp * self._slip(t) / 100
        exit_px = ltp - slip if buy else ltp + slip
        gross = (exit_px - t["entry"]) * t["qty"] * (1 if buy else -1)
        costs = self._charges(t, t["entry"], exit_px, t["qty"])
        rest = gross - costs
        self.data["capital"] = round(self.data["capital"] + rest, 2)
        net = round(rest + t.get("booked", 0), 2)  # whole trade, incl. half booked earlier
        t.update(exit=round(exit_px, 2), net=net, reason=reason,
                 r_mult=round(net / (t["risk_unit"] * t.get("qty0", t["qty"])), 2),
                 closed=now.isoformat(timespec="minutes"))
        self.data["open"].remove(t)
        self.data["closed"].append(t)
        icon = "✅" if net > 0 else "❌"
        return (f"{icon} PAPER #{t['id']} {t['symbol']} closed ({reason}) @ {exit_px:.2f} "
                f"| Net Rs {net:,.0f} ({t['r_mult']:+}R) | Capital Rs {self.data['capital']:,.0f}")

    def remove(self, trade_id):
        """Drop a trade with no P&L."""
        for t in self.data["open"]:
            if t["id"] == int(trade_id):
                self.data["open"].remove(t)
                self.save()
                return f"📝 PAPER #{trade_id} removed"
        return f"No open paper trade #{trade_id}"

    # ---------- reports ----------
    def positions_text(self):
        if not self.data["open"]:
            return "No open paper trades."
        lines = ["📝 Open paper trades:"]
        for t in self.data["open"]:
            if t["pending"]:
                lines.append(f"#{t['id']} {t['side']} {t['symbol']} - waiting for open")
            else:
                lines.append(f"#{t['id']} {t['kind']} {t['side']} {t['symbol']} x{t['qty']} "
                             f"@ {t['entry']} | SL {t['sl']} | Tgt {t['target']}")
        return "\n".join(lines)

    def pnl_text(self, now=None):
        now = now or datetime.now(IST)
        today = now.date().isoformat()
        closed = self.data["closed"]

        def stats(rows):
            if not rows:
                return "0 trades"
            wins = sum(1 for r in rows if r["net"] > 0)
            net = sum(r["net"] for r in rows)
            rs = [r["r_mult"] for r in rows if "r_mult" in r]
            avg = f", avg {sum(rs) / len(rs):+.2f}R" if rs else ""
            return f"{len(rows)} trades, win rate {wins / len(rows):.0%}, net Rs {net:,.0f}{avg}"

        lines = ["📊 Paper trading P&L",
                 f"Today: {stats([r for r in closed if r['closed'].startswith(today)])}",
                 f"Overall: {stats(closed)}"]
        for kind in ("SWING", "INTRADAY", "SCALP", "OPT_BUY"):
            rows = [r for r in closed if r["kind"] == kind]
            if rows:
                lines.append(f"  {kind}: {stats(rows)}")
        mtm = 0.0
        for t in self.data["open"]:
            if not t.get("pending") and t.get("ltp") is not None:
                mtm += (t["ltp"] - t["entry"]) * t["qty"] * (1 if t["side"] == "BUY" else -1)
        n_open = sum(1 for t in self.data["open"] if not t.get("pending"))
        lines.append(f"Open: {n_open} trades, unrealised Rs {mtm:,.0f} (before charges)")
        cap = self.data["capital"]
        lines.append(f"Capital: Rs {cap:,.0f} ({(cap / self.start_capital - 1):+.1%})")
        return "\n".join(lines)

    def reset(self):
        self.data = {"capital": self.start_capital, "next_id": 1, "open": [], "closed": []}
        self.save()
        return "📝 Paper account reset."
      
