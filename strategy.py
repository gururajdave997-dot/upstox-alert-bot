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


def _t(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------- Trend bias of an index (no volume needed) ----------
def trend_bias(df):
    """+1 / -1 / 0 from 5-min candles: close vs EMA21, EMA9 vs EMA21, close vs today's open."""
    if df is None or len(df) < C.EMA_SLOW + 5:
        return 0
    c = df["close"]
    e9, e21 = ema(c, C.EMA_FAST).iloc[-1], ema(c, C.EMA_SLOW).iloc[-1]
    day_open = df[df.index.date == df.index[-1].date()]["open"].iloc[0]
    last = c.iloc[-1]
    if last > e21 and e9 > e21 and last > day_open:
        return 1
    if last < e21 and e9 < e21 and last < day_open:
        return -1
    return 0


def daily_regime(df):
    """+1 if NIFTY closes above its 50 DMA, -1 below."""
    if df is None or len(df) < 50:
        return 0
    return 1 if df["close"].iloc[-1] > df["close"].rolling(50).mean().iloc[-1] else -1


# ---------- Intraday 5-min: EMA 9/21 cross + volume surge + VWAP side ----------
def intraday_signal(df, name, key, mkt=0):
    if df is None or len(df) < C.EMA_SLOW + 22:
        return None
    t = df.index[-1]
    if t.hour * 60 + t.minute > _t(C.INTRA_LAST_ENTRY):
        return None  # too late for a 1:2 target before square-off
    e9, e21 = ema(df["close"], C.EMA_FAST), ema(df["close"], C.EMA_SLOW)
    d = _cross(e9, e21)
    if not d:
        return None
    if C.MARKET_FILTER and mkt and mkt != d:
        return None  # against NIFTY's trend
    if (e21.iloc[-1] - e21.iloc[-4]) * d <= 0:
        return None  # slow EMA not sloping our way = choppy cross
    last = df.iloc[-1]
    vavg = df["volume"].rolling(20).mean().shift(1).iloc[-1]
    if not vavg or last["volume"] < C.VOLUME_MULTIPLIER * vavg:
        return None
    vw = vwap(df).iloc[-1]
    if (d > 0 and last["close"] < vw) or (d < 0 and last["close"] > vw):
        return None
    a = float(atr(df).iloc[-1])
    if abs(last["close"] - last["open"]) > C.INTRA_MAX_CANDLE_ATR * a:
        return None  # chasing a spike candle
    entry = float(last["close"])
    recent = df.tail(3)
    sl = float(recent["low"].min() if d > 0 else recent["high"].max())  # 3-candle swing
    risk = max(abs(entry - sl), a, entry * C.INTRA_MIN_RISK_PCT / 100)
    if risk > entry * C.INTRA_MAX_RISK_PCT / 100:
        return None
    return _base("INTRADAY", name, d, entry, entry - d * risk, entry + d * C.INTRADAY_RR * risk,
                 key, True, vol_ratio=round(float(last["volume"] / vavg), 1),
                 candle=df.index[-1].strftime("%H:%M"),
                 key=f"INTRA-{name}-{d}-{df.index[-1]}")


# ---------- Scalping 1-min: EMA 5/13 cross + VWAP/volume (stocks) or RSI (index) ----------
def scalp_signal(df, name, key, is_index, mkt=0):
    if df is None or len(df) < 40:
        return None
    d = _cross(ema(df["close"], C.SCALP_EMA_FAST), ema(df["close"], C.SCALP_EMA_SLOW))
    if not d:
        return None
    last = df.iloc[-1]
    a = float(atr(df).iloc[-1])
    if not is_index:
        if C.MARKET_FILTER and mkt and mkt != d:
            return None
        if a / float(last["close"]) * 100 < C.SCALP_MIN_ATR_PCT:
            return None  # moves too small: costs would eat the target
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
    entry = float(last["close"])
    return _base("SCALP", name, d, entry, entry - d * C.SCALP_ATR_SL * a,
                 entry + d * C.SCALP_ATR_TGT * a, key, True, note=note,
                 candle=df.index[-1].strftime("%H:%M"), key=f"SCALP-{name}-{d}-{df.index[-1]}")


# ---------- Swing daily: 20-day breakout/breakdown + volume + 50/200 DMA trend ----------
def swing_signal(df, name, key, regime=0):
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
    if d < 0 and not C.SWING_SHORTS:
        return None  # cash shares cannot be held short overnight
    if C.SWING_REGIME_FILTER and regime and regime != d:
        return None  # against the NIFTY 50-DMA regime
    turnover_cr = float((prev["close"] * prev["volume"]).tail(20).mean()) / 1e7
    if turnover_cr < C.SWING_MIN_TURNOVER_CR:
        return None  # illiquid: slippage and gaps hurt
    a = float(atr(df).iloc[-1])
    level = hh if up else ll
    if abs(last["close"] - level) > C.SWING_MAX_EXT_ATR * a:
        return None  # already ran too far from the breakout level
    rng = float(last["high"] - last["low"]) or 1e-9
    loc = float(last["close"] - last["low"]) / rng
    if (d > 0 and loc < C.SWING_CLOSE_LOC) or (d < 0 and loc > 1 - C.SWING_CLOSE_LOC):
        return None  # weak close: breakout not held into the close
    r = float(rsi(df["close"]).iloc[-1])
    if (d > 0 and r > 78) or (d < 0 and r < 22):
        return None  # exhausted
    entry = float(last["close"])
    risk = C.SWING_ATR_SL * a
    trend = "50>200 DMA" if d200 is not None else "above 50 DMA"
    return _base("SWING", name, d, entry, entry - d * risk, entry + d * C.SWING_RR * risk, key,
                 False, level=round(float(level), 2),
                 vol_ratio=round(float(last["volume"] / vavg), 1),
                 note=(trend if up else trend.replace(">", "<").replace("above", "below"))
                 + f" · RSI {r:.0f}",
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


def options_buy_setup(df, ctx, spot_trend=0):
    if ctx["bias"] == "NEUTRAL":
        return None
    d = 1 if ctx["bias"] == "BULLISH" else -1
    if C.OPT_CONFIRM and spot_trend != d:
        return None  # OI says one thing, price is not moving that way yet
    pick = S.best_buy(df, "CE" if d > 0 else "PE", ctx["spot"], strict=True)
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
