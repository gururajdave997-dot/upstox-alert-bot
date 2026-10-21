"""Tracks alerted trades and sends exit alerts: SL, breakeven, target, trailing SL,
opposite signal, spot breach (spreads), and intraday square-off. Saved to JSON."""
import html
import json
import logging
import os

import config as C

log = logging.getLogger(__name__)


class Tracker:
    def __init__(self, path):
        self.path = path
        self.pos, self.next_id = {}, 1
        try:
            with open(path) as f:
                d = json.load(f)
            self.pos, self.next_id = d.get("pos", {}), d.get("next_id", 1)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Could not load positions: %s", e)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as f:
                json.dump({"pos": self.pos, "next_id": self.next_id}, f)
        except Exception as e:
            log.warning("Could not save positions: %s", e)

    def drop_stale_intraday(self, today):
        stale = [k for k, p in self.pos.items() if p["intraday"] and p["opened"][:10] != str(today)]
        for k in stale:
            del self.pos[k]
        if stale:
            self._save()

    @staticmethod
    def _label(p):
        return f"#{p['id']} {html.escape(p['symbol'])} {p['label']}"

    def open(self, sig, now):
        """Returns (position_id or None, messages). None id + no msgs = duplicate, skip alert."""
        msgs = []
        has_watch = sig.get("watch_key") or sig.get("legs")
        for pid, p in list(self.pos.items()):
            if p["symbol"] == sig["symbol"] and p["kind"] == sig["kind"]:
                if sig["direction"] and p["direction"] == -sig["direction"]:
                    msgs.append(f"🔄 <b>EXIT {self._label(p)}</b> – opposite signal appeared.")
                    del self.pos[pid]
                elif p["direction"] == sig["direction"]:
                    return None, ["DUP"]
        if not has_watch:
            self._save()
            return None, msgs
        pid = str(self.next_id)
        self.next_id += 1
        label = sig.get("strategy") or (
            f"{sig['pick']['strike']} {sig['pick']['opt']} BUY" if sig["kind"] == "OPT_BUY"
            else f"{sig['kind']} {sig['side']}")
        self.pos[pid] = {
            "id": pid, "kind": sig["kind"], "symbol": sig["symbol"], "label": label,
            "direction": sig["direction"], "fav": sig["fav"], "mode": sig["mode"],
            "watch_key": sig.get("watch_key"),
            "legs": [{"act": l["act"], "key": l["key"]} for l in sig.get("legs", [])],
            "entry": sig["entry"], "sl": sig["sl"], "sl0": sig["sl"], "target": sig["target"],
            "best": sig["entry"], "be": False, "tgt": False, "trail_alert": sig["sl"],
            "intraday": sig["intraday"], "guard": sig.get("guard"), "opened": now.isoformat()}
        self._save()
        return pid, msgs

    def keys_needed(self):
        ks = set()
        for p in self.pos.values():
            if p["watch_key"]:
                ks.add(p["watch_key"])
            ks.update(l["key"] for l in p["legs"])
            if p.get("guard"):
                ks.add(p["guard"]["key"])
        return ks

    def _value(self, p, px):
        if p["mode"] == "price":
            return px.get(p["watch_key"])
        try:
            return sum(px[l["key"]] * (1 if l["act"] == "SELL" else -1) for l in p["legs"])
        except KeyError:
            return None

    def _pnl(self, p, v):
        if p["mode"] == "spread":
            return f"{p['entry'] - v:+.2f} pts"
        return f"{(v - p['entry']) * p['fav'] / p['entry'] * 100:+.2f}%"

    def check(self, px):
        msgs = []
        for pid, p in list(self.pos.items()):
            v = self._value(p, px)
            if v is None:
                continue
            v = round(v, 2)
            f, lab = p["fav"], self._label(p)
            g = p.get("guard")
            if g and px.get(g["key"]) is not None:
                s = px[g["key"]]
                if (g.get("upper") and s >= g["upper"]) or (g.get("lower") and s <= g["lower"]):
                    msgs.append(f"🛑 <b>EXIT {lab}</b> – spot {s} breached short strike. "
                                f"Spread {v} ({self._pnl(p, v)})")
                    del self.pos[pid]
                    continue
            if (v - p["sl"]) * f <= 0:
                why = "trailing SL hit" if (p["sl"] - p["sl0"]) * f > 0 else "stop-loss hit"
                msgs.append(f"🛑 <b>EXIT {lab}</b> – {why} @ {v} ({self._pnl(p, v)})")
                del self.pos[pid]
                continue
            if p["mode"] == "spread":
                if (v - p["target"]) * f >= 0:
                    msgs.append(f"🎯 <b>BOOK PROFIT {lab}</b> – {C.SELL_TARGET_PCT:.0f}% of credit "
                                f"captured. Spread {v} ({self._pnl(p, v)})")
                    del self.pos[pid]
                continue
            risk = abs(p["entry"] - p["sl0"]) or 1e-9
            if (v - p["best"]) * f > 0:
                p["best"] = v
            if not p["be"] and (v - p["entry"]) * f >= risk:
                p["be"] = True
                if (p["entry"] - p["sl"]) * f > 0:
                    p["sl"] = p["entry"]
                    p["trail_alert"] = p["sl"]
                msgs.append(f"🔒 {lab}: +1R reached @ {v}. Move SL to cost {p['sl']}.")
            if not p["tgt"] and (v - p["target"]) * f >= 0:
                p["tgt"] = True
                msgs.append(f"🎯 <b>TARGET {lab}</b> @ {v} ({self._pnl(p, v)}). "
                            f"Book 50%, trail the rest.")
            if p["be"]:
                new_sl = round(p["best"] - f * C.TRAIL_R * risk, 2)
                if (new_sl - p["sl"]) * f > 0:
                    p["sl"] = new_sl
                    if abs(new_sl - p["trail_alert"]) >= 0.5 * risk:
                        p["trail_alert"] = new_sl
                        msgs.append(f"📐 {lab}: trailing SL → {new_sl} (LTP {v})")
        if msgs:
            self._save()
        return msgs

    def square_off(self):
        out = [f"⏰ <b>SQUARE OFF {self._label(p)}</b> (intraday)" for p in self.pos.values()
               if p["intraday"]]
        self.pos = {k: p for k, p in self.pos.items() if not p["intraday"]}
        self._save()
        return out

    def close(self, pid):
        p = self.pos.pop(pid, None)
        self._save()
        return f"✖️ Stopped tracking {self._label(p)}" if p else f"No open position #{pid}"

    def summary(self):
        if not self.pos:
            return "No tracked positions."
        return "<b>Tracked positions</b>\n" + "\n".join(
            f"{self._label(p)} | entry {p['entry']} SL {p['sl']} tgt {p['target']}"
            for p in self.pos.values())
