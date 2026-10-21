"""Signals: intraday (5m), scalping (1m), swing (daily), options buy & sell setups."""
import config as C
import strike_selector as S
from indicators import atr, ema, rsi, vwap


def _cross(f, s):
    if f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]:
        return 1
    if f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1]:
        return -1
    return 0


def _base(kind, name, d, entry, sl, target, watch_key, intraday, **extra):
    return {"kind": kind, "symbol": name, "direction": d, "fav": d, "mode": "price",
            "side": "BUY" if d > 0 else "SELL", "entry": round(entry, 2), "sl": round(sl, 2),
            "target": round(target, 2), "watch_key": watch_key, "intraday": intraday, **extra}


# ---------- Intraday 5-min: EMA 9/21 cross + volume surge + VWAP side ----------
def intraday_signal(df, name, key):
    if df is None or len(df) < C.EMA_SLOW + 22:
        return None
    d = _cross(ema(df["close"], C.EMA_FAST), ema(df["close"], C.EMA_SLOW))
    if not d:
        return None
    last = df.iloc[-1]
    vavg = df["volume"].rolling(20).mean().shift(1).iloc[-1]
    if not vavg or last["volume"] < C.VOLUME_MULTIPLIER * vavg:
        return None
    vw = vwap(df).iloc[-1]
    if (d > 0 and last["close"] < vw) or (d < 0 and last["close"] > vw):
        return None
    entry = float(last["close"])
    sl = float(last["low"] if d > 0 else last["high"])
    risk = max(abs(entry - sl), float(atr(df).iloc[-1]), entry * 0.002)
    return _base("INTRADAY", name, d, entry, entry - d * risk, entry + d * C.INTRADAY_RR * risk,
                 key, True, vol_ratio=round(float(last["volume"] / vavg), 1),
                 candle=df.index[-1].strftime("%H:%M"),
                 key=f"INTRA-{name}-{d}-{df.index[-1]}")


# ---------- Scalping 1-min: EMA 5/13 cross + VWAP/volume (stocks) or RSI (index) ----------
def scalp_signal(df, name, key, is_index):
    if df is None or len(df) < 40:
        return None
    d = _cross(ema(df["close"], C.SCALP_EMA_FAST), ema(df["close"], C.SCALP_EMA_SLOW))
    if not d:
        return None
    last = df.iloc[-1]
    if is_index:
        r = rsi(df["close"], 14).iloc[-1]
        if (d > 0 and r < 55) or (d < 0 and r > 45):
            return None
        note = f"RSI {r:.0f}"
    else:
        vavg = df["volume"].rolling(20).mean().shift(1).iloc[-1]
        vw = vwap(df).iloc[-1]
        if not vavg or last["volume"] < C.VOLUME_MULTIPLIER * vavg:
            return None
        if (d > 0 and last["close"] < vw) or (d < 0 and last["close"] > vw):
            return None
        note = f"Vol {last['volume'] / vavg:.1f}x, {'above' if d > 0 else 'below'} VWAP"
    a = float(atr(df).iloc[-1])
    entry = float(last["close"])
    return _base("SCALP", name, d, entry, entry - d * C.SCALP_ATR_SL * a,
                 entry + d * C.SCALP_ATR_TGT * a, key, True, note=note,
                 candle=df.index[-1].strftime("%H:%M"), key=f"SCALP-{name}-{d}-{df.index[-1]}")


# ---------- Swing daily: 20-day breakout/breakdown + volume + 50/200 DMA trend ----------
def swing_signal(df, name, key):
    if df is None or len(df) < 60:
        return None
    last, prev = df.iloc[-1], df.iloc[:-1]
    hh = prev["high"].tail(C.SWING_LOOKBACK).max()
    ll = prev["low"].tail(C.SWING_LOOKBACK).min()
    vavg = prev["volume"].tail(20).mean()
    d50 = df["close"].rolling(50).mean().iloc[-1]
    d200 = df["close"].rolling(200).mean().iloc[-1] if len(df) >= 200 else None
    vol_ok = vavg and last["volume"] >= C.VOLUME_MULTIPLIER * vavg
    up = last["close"] > hh and last["close"] > d50 and (d200 is None or d50 > d200)
    dn = last["close"] < ll and last["close"] < d50 and (d200 is None or d50 < d200)
    if not vol_ok or not (up or dn):
        return None
    d = 1 if up else -1
    a = float(atr(df).iloc[-1])
    entry = float(last["close"])
    risk = C.SWING_ATR_SL * a
    trend = "50>200 DMA" if d200 is not None else "above 50 DMA"
    return _base("SWING", name, d, entry, entry - d * risk, entry + d * C.SWING_RR * risk, key,
                 False, level=round(float(hh if up else ll), 2),
                 vol_ratio=round(float(last["volume"] / vavg), 1),
                 note=(trend if up else trend.replace(">", "<").replace("above", "below")),
                 key=f"SWING-{name}-{d}-{df.index[-1].date()}")


# ---------- Options chain analysis ----------
def analyse_chain(df, name, expiry, underlying_key):
    spot = float(df["spot"].dropna().iloc[0])
    i = int((df["strike"] - spot).abs().idxmin())
    w = df.iloc[max(0, i - C.STRIKES_AROUND_ATM): i + C.STRIKES_AROUND_ATM + 1]
    ce_oi, pe_oi = w["ce_oi"].sum(), w["pe_oi"].sum()
    pcr = pe_oi / ce_oi if ce_oi else 0
    ce_chg = int((w["ce_oi"] - w["ce_prev_oi"]).sum())
    pe_chg = int((w["pe_oi"] - w["pe_prev_oi"]).sum())
    if pcr >= C.PCR_BULLISH and pe_chg > ce_chg and pe_chg > 0:
        bias = "BULLISH"
    elif pcr <= C.PCR_BEARISH and ce_chg > pe_chg and ce_chg > 0:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    atm = df.iloc[i]
    wide = df.iloc[max(0, i - 2 * C.STRIKES_AROUND_ATM): i + 2 * C.STRIKES_AROUND_ATM + 1]
    above = wide[wide["strike"] >= atm["strike"]]
    below = wide[wide["strike"] <= atm["strike"]]
    ivs = [v for v in (atm["ce_iv"], atm["pe_iv"]) if v]
    return {"symbol": name, "expiry": expiry, "underlying": underlying_key, "spot": round(spot, 2),
            "pcr": round(pcr, 2), "ce_chg": ce_chg, "pe_chg": pe_chg, "bias": bias,
            "resistance": int(above.loc[above["ce_oi"].idxmax(), "strike"]),
            "support": int(below.loc[below["pe_oi"].idxmax(), "strike"]),
            "atm": int(atm["strike"]), "atm_iv": round(sum(ivs) / len(ivs), 2) if ivs else 0}


def options_buy_setup(df, ctx):
    if ctx["bias"] == "NEUTRAL":
        return None
    d = 1 if ctx["bias"] == "BULLISH" else -1
    pick = S.best_buy(df, "CE" if d > 0 else "PE", ctx["spot"])
    if not pick or pick["ltp"] <= 0:
        return None
    ltp = pick["ltp"]
    return {**ctx, "kind": "OPT_BUY", "direction": d, "fav": 1, "mode": "price", "pick": pick,
            "entry": ltp, "sl": round(ltp * (1 - C.OPT_SL_PCT / 100), 2),
            "target": round(ltp * (1 + C.OPT_TARGET_PCT / 100), 2), "watch_key": pick["key"],
            "intraday": True, "key": f"OB-{ctx['symbol']}-{pick['strike']}{pick['opt']}"}


def options_sell_setup(df, ctx):
    """BULLISH -> Bull Put Spread, BEARISH -> Bear Call Spread, NEUTRAL (+IV) -> Iron Condor."""
    bias, spot = ctx["bias"], ctx["spot"]
    legs = []
    if bias in ("BULLISH", "NEUTRAL"):
        s = S.best_sell(df, "PE", spot, ctx["support"])
        h = s and S.hedge(df, "PE", s["strike"])
        if s and h:
            legs += [("SELL", s), ("BUY", h)]
    if bias in ("BEARISH", "NEUTRAL"):
        s = S.best_sell(df, "CE", spot, ctx["resistance"])
        h = s and S.hedge(df, "CE", s["strike"])
        if s and h:
            legs += [("SELL", s), ("BUY", h)]
    if bias == "NEUTRAL" and (len(legs) < 4 or ctx["atm_iv"] < C.IV_SELL_MIN):
        return None
    if not legs:
        return None
    credit = sum(l["ltp"] for a, l in legs if a == "SELL") - sum(l["ltp"] for a, l in legs if a == "BUY")
    width = max(abs(legs[i][1]["strike"] - legs[i + 1][1]["strike"]) for i in range(0, len(legs), 2))
    if credit <= 0 or credit < width * C.MIN_CREDIT_PCT / 100:
        return None  # poor reward vs risk
    shorts = {l["opt"]: l["strike"] for a, l in legs if a == "SELL"}
    name = {"BULLISH": "BULL PUT SPREAD", "BEARISH": "BEAR CALL SPREAD"}.get(bias, "IRON CONDOR")
    be = []
    if "PE" in shorts:
        be.append(round(shorts["PE"] - credit, 1))
    if "CE" in shorts:
        be.append(round(shorts["CE"] + credit, 1))
    return {**ctx, "kind": "OPT_SELL", "strategy": name,
            "direction": {"BULLISH": 1, "BEARISH": -1}.get(bias, 0), "fav": -1, "mode": "spread",
            "legs": [{"act": a, **l} for a, l in legs], "entry": round(credit, 2),
            "sl": round(credit * (1 + C.SELL_SL_PCT / 100), 2),
            "target": round(credit * (1 - C.SELL_TARGET_PCT / 100), 2),
            "max_loss": round(width - credit, 2), "breakevens": be, "intraday": True,
            "guard": {"key": ctx["underlying"], "lower": shorts.get("PE"), "upper": shorts.get("CE")},
            "key": f"OS-{ctx['symbol']}-{name}-{'-'.join(str(v) for v in shorts.values())}"}


def iv_spike(ctx, state):
    iv, prev = ctx["atm_iv"], state.get(ctx["symbol"])
    state[ctx["symbol"]] = iv or prev
    if iv and prev and (iv - prev) / prev * 100 >= C.IV_SPIKE_PCT:
        return {**ctx, "prev_iv": prev}
    return None
