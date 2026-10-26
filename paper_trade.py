"""
paper_trade.py - virtual (paper) trading ledger for the Upstox Alert Bot.
No real orders are ever placed. It only uses live prices the bot already fetches.

Settings (add to BOT_SETTINGS, one per line):
  PAPER_TRADING=ON          turn paper trading on
  PAPER_CAPITAL=500000      starting virtual capital (Rs)
  PAPER_RISK_PCT=1          % of capital risked per trade
  PAPER_SLIPPAGE_PCT=0.05   price slippage on entry and exit
  PAPER_COST_PCT=0.1        round-trip charges for stocks, % of turnover
  PAPER_OPTION_COST=60      flat round-trip charges per options trade (Rs)
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
                  "PAPER_COST_PCT", "PAPER_OPTION_COST"):
            if k not in s and getattr(config, k, None) is not None:
                s[k] = str(getattr(config, k))
    except Exception:
        pass
    for k, v in os.environ.items():
        if k.startswith("PAPER_") and k not in s:
            s[k] = v
    return s


class PaperBook:
    def __init__(self, path=LEDGER_FILE, square_off=SQUARE_OFF):
        cfg = _settings()
        self.enabled = cfg.get("PAPER_TRADING", "OFF").upper() == "ON"
        self.start_capital = float(cfg.get("PAPER_CAPITAL", 500000))
        self.risk_pct = float(cfg.get("PAPER_RISK_PCT", 1))
        self.slip_pct = float(cfg.get("PAPER_SLIPPAGE_PCT", 0.05))
        self.cost_pct = float(cfg.get("PAPER_COST_PCT", 0.1))
        self.option_cost = float(cfg.get("PAPER_OPTION_COST", 60))
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

    # ---------- trades ----------
    def open_trade(self, kind, symbol, ikey, side, sl, target, ltp=None,
                   lot_size=1, is_option=False, now=None):
        """Call when an alert fires. ikey = Upstox instrument key whose price is tracked.
        Before 9:15 (or ltp=None) the trade waits and fills at the first live price."""
        if not self.enabled:
            return None
        now = now or datetime.now(IST)
        t = {"id": self.data["next_id"], "kind": kind.upper(), "symbol": symbol, "ikey": ikey,
             "side": side.upper(), "sl": float(sl), "target": float(target),
             "lot": int(lot_size), "is_option": is_option, "pending": True,
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
        slip = ltp * self.slip_pct / 100
        entry = ltp + slip if buy else ltp - slip
        risk_unit = abs(entry - t["sl"])
        cap = self.data["capital"]
        qty = int((cap * self.risk_pct / 100) // risk_unit) if risk_unit > 0 else 0
        qty = min(qty, int(cap // entry))           # cannot spend more than capital
        qty = (qty // t["lot"]) * t["lot"]          # whole lots for options
        if qty <= 0:
            self.data["open"].remove(t)
            return f"📝 PAPER #{t['id']} {t['symbol']} skipped: quantity too small"
        t.update(pending=False, entry=round(entry, 2), qty=qty,
                 risk_unit=risk_unit, opened=now.isoformat(timespec="minutes"))
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
            buy = t["side"] == "BUY"
            r = t["risk_unit"]
            # trailing: after a 1R move in favour, move SL to entry (no-loss)
            if buy and ltp >= t["entry"] + r and t["sl"] < t["entry"]:
                t["sl"] = t["entry"]
            if not buy and ltp <= t["entry"] - r and t["sl"] > t["entry"]:
                t["sl"] = t["entry"]
            reason = None
            if (buy and ltp <= t["sl"]) or (not buy and ltp >= t["sl"]):
                reason = "SL hit"
            elif (buy and ltp >= t["target"]) or (not buy and ltp <= t["target"]):
                reason = "Target hit"
            elif t["kind"] != "SWING" and now.time() >= self.square_off:
                reason = "3:15 PM square-off"
            if reason:
                msgs.append(self._close(t, ltp, reason, now))
        if msgs:
            self.save()
        return msgs

    def _close(self, t, ltp, reason, now):
        buy = t["side"] == "BUY"
        slip = ltp * self.slip_pct / 100
        exit_px = ltp - slip if buy else ltp + slip
        gross = (exit_px - t["entry"]) * t["qty"] * (1 if buy else -1)
        costs = self.option_cost if t["is_option"] else \
            (t["entry"] + exit_px) * t["qty"] * self.cost_pct / 100
        net = round(gross - costs, 2)
        self.data["capital"] = round(self.data["capital"] + net, 2)
        t.update(exit=round(exit_px, 2), net=net, reason=reason,
                 closed=now.isoformat(timespec="minutes"))
        self.data["open"].remove(t)
        self.data["closed"].append(t)
        icon = "✅" if net > 0 else "❌"
        return (f"{icon} PAPER #{t['id']} {t['symbol']} closed ({reason}) @ {exit_px:.2f} "
                f"| Net Rs {net:,.0f} | Capital Rs {self.data['capital']:,.0f}")

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
            return f"{len(rows)} trades, win rate {wins / len(rows):.0%}, net Rs {net:,.0f}"

        lines = ["📊 Paper trading P&L",
                 f"Today: {stats([r for r in closed if r['closed'].startswith(today)])}",
                 f"Overall: {stats(closed)}"]
        for kind in ("SWING", "INTRADAY", "SCALP", "OPT_BUY"):
            rows = [r for r in closed if r["kind"] == kind]
            if rows:
                lines.append(f"  {kind}: {stats(rows)}")
        cap = self.data["capital"]
        lines.append(f"Capital: Rs {cap:,.0f} ({(cap / self.start_capital - 1):+.1%})")
        return "\n".join(lines)

    def reset(self):
        self.data = {"capital": self.start_capital, "next_id": 1, "open": [], "closed": []}
        self.save()
        return "📝 Paper account reset."
      
