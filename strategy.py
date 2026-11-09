"""Signals: intraday (5m), swing (daily), options intraday buy, options swing, option sell setups."""
import config as C
import strike_selector as S
from indicators import adx, atr, day_change_pct, ema, resample, rsi, vwap


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


# ---------- Intraday 5-min: ORB / trend PULLBACK / EMA CROSS, with quality filters ----------
def _orb(df, d_hint=0):
    """Opening-range breakout: first fresh close beyond the first ORB_MINUTES range."""
    t = df.index[-1]
    today = df[df.index.date == t.date()]
    n = max(1, C.ORB_MINUTES // C.CANDLE_MINUTES)
    if len(today) <= n or t.hour * 60 + t.minute > _t(C.ORB_LAST_ENTRY):
        return 0, None
    rng = today.iloc[:n]
    hi, lo = float(rng["high"].max()), float(rng["low"].min())
    if (hi - lo) / lo * 100 > C.ORB_MAX_WIDTH_PCT:
        return 0, None
    after = today.iloc[n:]
    c, pc = float(after["close"].iloc[-1]), float(today["close"].iloc[-2])
    if c > hi and pc <= hi and (after["close"].iloc[:-1] <= hi).all():
        return 1, (hi, lo)
    if c < lo and pc >= lo and (after["close"].iloc[:-1] >= lo).all():
        return -1, (hi, lo)
    return 0, None


def _pullback(df, e9, e21, vw):
    """Trend continuation: EMA9 > EMA21 rising, a recent dip into EMA21/VWAP, then a strong
    candle closing back above the previous high (mirror for shorts)."""
    last, prev = df.iloc[-1], df.iloc[-2]
    for d in (1, -1):
        trend = (e9.iloc[-1] - e21.iloc[-1]) * d > 0 and (e21.iloc[-1] - e21.iloc[-4]) * d > 0
        if not trend:
            continue
        zone = [max(e21.iloc[-i], vw.iloc[-i]) if d > 0 else min(e21.iloc[-i], vw.iloc[-i])
                for i in (2, 3)]
        lows = [df["low"].iloc[-2], df["low"].iloc[-3]] if d > 0 else \
            [df["high"].iloc[-2], df["high"].iloc[-3]]
        touched = any((lw - z * (1 + d * 0.001)) * d <= 0 for lw, z in zip(lows, zone))
        resumed = (last["close"] - last["open"]) * d > 0 and \
            (last["close"] - (prev["high"] if d > 0 else prev["low"])) * d > 0 and \
            (last["close"] - e9.iloc[-1]) * d > 0
        if touched and resumed:
            return d
    return 0


def intraday_signal(df, name, key, mkt=0, mkt_chg=None):
    if df is None or len(df) < C.EMA_SLOW + 22:
        return None
    t = df.index[-1]
    if t.hour * 60 + t.minute > _t(C.INTRA_LAST_ENTRY):
        return None  # too late for a 1:2 target before square-off
    c = df["close"]
    e9, e21, vw = ema(c, C.EMA_FAST), ema(c, C.EMA_SLOW), vwap(df)
    setup, d, orb = None, 0, None
    if "ORB" in C.INTRA_SETUPS:
        d, orb = _orb(df)
        setup = "ORB" if d else None
    if not d and "PULLBACK" in C.INTRA_SETUPS:
        d = _pullback(df, e9, e21, vw)
        setup = "PULLBACK" if d else None
    if not d and "CROSS" in C.INTRA_SETUPS:
        d = _cross(e9, e21)
        if d and (e21.iloc[-1] - e21.iloc[-4]) * d <= 0:
            d = 0  # slow EMA not sloping our way = choppy cross
        setup = "CROSS" if d else None
    if not d:
        return None
    if C.MARKET_FILTER and mkt and mkt != d:
        return None  # against NIFTY's trend
    last = df.iloc[-1]
    vavg = df["volume"].rolling(20).mean().shift(1).iloc[-1]
    need = C.PULLBACK_VOL_MULT if setup == "PULLBACK" else C.VOLUME_MULTIPLIER
    if not vavg or last["volume"] < need * vavg:
        return None
    if (last["close"] - vw.iloc[-1]) * d < 0:
        return None  # wrong side of VWAP
    a = float(atr(df).iloc[-1])
    if abs(last["close"] - last["open"]) > C.INTRA_MAX_CANDLE_ATR * a:
        return None  # chasing a spike candle
    r = float(rsi(c).iloc[-1])
    if (d > 0 and r > C.INTRA_RSI_MAX) or (d < 0 and r < 100 - C.INTRA_RSI_MAX):
        return None  # stretched: late entry
    strength = float(adx(df).iloc[-1])
    if setup != "ORB" and strength < C.INTRA_MIN_ADX:
        return None  # no trend: crosses/pullbacks fail in chop
    if C.INTRA_HTF_FILTER:
        h = resample(df, 15)
        if len(h) >= C.EMA_SLOW + 2:
            h9, h21 = ema(h["close"], C.EMA_FAST).iloc[-1], ema(h["close"], C.EMA_SLOW).iloc[-1]
            if (h9 - h21) * d < 0:
                return None  # 15-min trend disagrees
    chg = day_change_pct(df)
    if C.INTRA_RS_FILTER and mkt_chg is not None and (chg - mkt_chg) * d < 0:
        return None  # longs must be stronger than NIFTY today, shorts weaker
    entry = float(last["close"])
    recent = df.tail(3)
    sl = float(recent["low"].min() if d > 0 else recent["high"].max())  # 3-candle swing
    risk = max(abs(entry - sl), a, entry * C.INTRA_MIN_RISK_PCT / 100)
    if risk > entry * C.INTRA_MAX_RISK_PCT / 100:
        return None
    why = {"ORB": f"{C.ORB_MINUTES}m range {orb[1]:g}-{orb[0]:g} broken" if orb else "ORB",
           "PULLBACK": f"dip to EMA{C.EMA_SLOW}/VWAP, trend resumed",
           "CROSS": f"EMA{C.EMA_FAST}/{C.EMA_SLOW} cross"}[setup]
    rs = f" · RS {chg - mkt_chg:+.1f}% vs NIFTY" if mkt_chg is not None else ""
    return _base("INTRADAY", name, d, entry, entry - d * risk, entry + d * C.INTRADAY_RR * risk,
                 key, True, setup=setup, why=why, vol_ratio=round(float(last["volume"] / vavg), 1),
                 note=f"ADX {strength:.0f} · RSI {r:.0f}{rs}",
                 candle=df.index[-1].strftime("%H:%M"),
                 key=f"INTRA-{name}-{d}-{df.index[-1]}")


# ---------- Swing daily: 20-day BREAKOUT or trend PULLBACK to 20 EMA, + RS vs NIFTY ----------
def _ret(df, n):
    if df is None or len(df) <= n:
        return None
    return float(df["close"].iloc[-1] / df["close"].iloc[-1 - n] - 1) * 100


def swing_signal(df, name, key, regime=0, nifty=None):
    if df is None or len(df) < 60:
        return None
    last, prev = df.iloc[-1], df.iloc[:-1]
    c = df["close"]
    hh = prev["high"].tail(C.SWING_LOOKBACK).max()
    ll = prev["low"].tail(C.SWING_LOOKBACK).min()
    vavg = prev["volume"].tail(20).mean()
    sma50 = c.rolling(50).mean()
    d50 = sma50.iloc[-1]
    d200 = c.rolling(200).mean().iloc[-1] if len(df) >= 200 else None
    e20 = ema(c, 20)
    a = float(atr(df).iloc[-1])
    rsr = rsi(c)
    r = float(rsr.iloc[-1])
    vr = float(last["volume"] / vavg) if vavg else 0
    rng = float(last["high"] - last["low"]) or 1e-9
    loc = float(last["close"] - last["low"]) / rng
    setup, d, level, sl = None, 0, None, None
    # 1) Breakout / breakdown of the 20-day range on volume, with the trend
    if "BREAKOUT" in C.SWING_SETUPS and vr >= C.VOLUME_MULTIPLIER:
        up = last["close"] > hh and last["close"] > d50 and (d200 is None or d50 > d200)
        dn = last["close"] < ll and last["close"] < d50 and (d200 is None or d50 < d200)
        if up or dn:
            d, level = (1, hh) if up else (-1, ll)
            if abs(last["close"] - level) > C.SWING_MAX_EXT_ATR * a:
                d = 0  # already ran too far from the breakout level
            elif (d > 0 and loc < C.SWING_CLOSE_LOC) or (d < 0 and loc > 1 - C.SWING_CLOSE_LOC):
                d = 0  # weak close: breakout not held into the close
            elif (d > 0 and r > 78) or (d < 0 and r < 22):
                d = 0  # exhausted
            else:
                setup, sl = "BREAKOUT", float(last["close"]) - d * C.SWING_ATR_SL * a
    # 2) Pullback: uptrend (close > 50 DMA rising, 50 > 200), dipped to 20 EMA in the last
    #    3 sessions with RSI cooling to 40-60, today closes above yesterday's high
    if not d and "PULLBACK" in C.SWING_SETUPS:
        for dd in (1, -1):
            trend = (last["close"] - d50) * dd > 0 and (d50 - sma50.iloc[-10]) * dd > 0 and \
                (d200 is None or (d50 - d200) * dd > 0) and (e20.iloc[-1] - d50) * dd > 0
            if not trend:
                continue
            ext = df["low"].iloc[-4:-1] if dd > 0 else df["high"].iloc[-4:-1]
            touched = ((ext - (e20.iloc[-4:-1] + dd * 0.25 * a)) * dd <= 0).any()
            cooled = rsr.iloc[-4:-1].between(38, 62).any()  # momentum reset, not a collapse
            turn = (last["close"] - (prev["high"].iloc[-1] if dd > 0 else prev["low"].iloc[-1])) * dd > 0 \
                and (last["close"] - last["open"]) * dd > 0 and (last["close"] - e20.iloc[-1]) * dd > 0
            vol_ok = vr >= 1.0
            if touched and cooled and turn and vol_ok:
                d, setup = dd, "PULLBACK"
                swing = float(df["low"].tail(5).min() if dd > 0 else df["high"].tail(5).max())
                sl = swing - dd * 0.25 * a
                risk = abs(float(last["close"]) - sl)
                risk = min(max(risk, 1.0 * a), 2.5 * a)  # keep stop between 1 and 2.5 ATR
                sl = float(last["close"]) - dd * risk
                level = float(e20.iloc[-1])
                break
    if not d:
        return None
    if d < 0 and not C.SWING_SHORTS:
        return None  # cash shares cannot be held short overnight
    if C.SWING_REGIME_FILTER and regime and regime != d:
        return None  # against the NIFTY 50-DMA regime
    turnover_cr = float((prev["close"] * prev["volume"]).tail(20).mean()) / 1e7
    if turnover_cr < C.SWING_MIN_TURNOVER_CR:
        return None  # illiquid: slippage and gaps hurt
    rs_txt = ""
    sr, nr = _ret(df, C.SWING_RS_DAYS), _ret(nifty, C.SWING_RS_DAYS)
    if sr is not None and nr is not None:
        if C.SWING_RS_FILTER and (sr - nr) * d < 0:
            return None  # laggard: buy leaders, not laggards
        rs_txt = f" · RS {sr - nr:+.0f}% vs NIFTY ({C.SWING_RS_DAYS}d)"
    entry = float(last["close"])
    risk = abs(entry - sl)
    hi52 = float(df["high"].tail(250).max())
    near_high = f" · {(1 - entry / hi52) * 100:.0f}% below 52w high" if d > 0 else ""
    trend = "50>200 DMA" if d200 is not None else "above 50 DMA"
    if d < 0:
        trend = trend.replace(">", "<").replace("above", "below")
    return _base("SWING", name, d, entry, entry - d * risk, entry + d * C.SWING_RR * risk, key,
                 False, setup=setup, level=round(float(level), 2), vol_ratio=round(vr, 1),
                 atr=round(a, 2),
                 note=f"{trend} · RSI {r:.0f}{rs_txt}{near_high}",
                 key=f"SWING-{name}-{d}-{df.index[-1].date()}")


# ---------- Index daily trend setup (for positional option buys) ----------
def index_swing_signal(df, name, key):
    """Daily NIFTY/BANKNIFTY: trend (close > 20 EMA > 50 DMA, 20 EMA rising, ADX >= 18) + a
    20-day breakout or a dip-to-20-EMA reversal. Mirror for downtrends (buy PE)."""
    if df is None or len(df) < 60:
        return None
    c = df["close"]
    last, prev = df.iloc[-1], df.iloc[:-1]
    e20, s50 = ema(c, 20), c.rolling(50).mean()
    a = float(atr(df).iloc[-1])
    rsr = rsi(c)
    r = float(rsr.iloc[-1])
    strength = float(adx(df).iloc[-1])
    if strength < 18:
        return None  # flat market: bought options bleed theta
    rng = float(last["high"] - last["low"]) or 1e-9
    loc = float(last["close"] - last["low"]) / rng
    for d in (1, -1):
        trend = (last["close"] - e20.iloc[-1]) * d > 0 and (e20.iloc[-1] - s50.iloc[-1]) * d > 0 \
            and (e20.iloc[-1] - e20.iloc[-5]) * d > 0
        if not trend or (d > 0 and r > 75) or (d < 0 and r < 25):
            continue
        lvl = prev["high"].tail(20).max() if d > 0 else prev["low"].tail(20).min()
        brk = (last["close"] - lvl) * d > 0 and abs(last["close"] - lvl) <= 1.0 * a and \
            (loc >= 0.6 if d > 0 else loc <= 0.4)
        ext = df["low"].iloc[-4:-1] if d > 0 else df["high"].iloc[-4:-1]
        dip = ((ext - (e20.iloc[-4:-1] + d * 0.25 * a)) * d <= 0).any() and \
            rsr.iloc[-4:-1].between(38, 62).any()
        turn = (last["close"] - (prev["high"].iloc[-1] if d > 0 else prev["low"].iloc[-1])) * d > 0 \
            and (last["close"] - last["open"]) * d > 0
        setup = "BREAKOUT" if brk else ("PULLBACK" if dip and turn else None)
        if not setup:
            continue
        swing = float(df["low"].tail(3).min() if d > 0 else df["high"].tail(3).max())
        risk = abs(float(last["close"]) - (swing - d * 0.25 * a))
        risk = min(max(risk, 1.0 * a), 2.5 * a)
        return {"symbol": name, "underlying": key, "direction": d, "setup": setup,
                "spot": round(float(last["close"]), 2), "spot_risk": round(risk, 2),
                "spot_sl": round(float(last["close"]) - d * risk, 2), "atr": round(a, 2),
                "note": f"ADX {strength:.0f} · RSI {r:.0f}", "date": str(df.index[-1].date())}
    return None


def premium_levels(ltp, delta, spot_risk, rr, lo_pct, hi_pct):
    """Map an underlying stop to option premium via delta; clamp to sane % bands."""
    risk = (delta or 0.5) * spot_risk
    risk = min(max(risk, ltp * lo_pct / 100), ltp * hi_pct / 100)
    return round(ltp - risk, 2), round(ltp + rr * risk, 2), round(risk / ltp * 100)


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


def options_buy_setup(df, ctx, spot_trend=0, idx_df=None):
    """Intraday option buy: OI bias + index 5-min trend (with ADX strength) agree, room to the
    next OI wall, liquid strike. SL/target from the index's own structure, mapped by delta."""
    if ctx["bias"] == "NEUTRAL":
        return None
    d = 1 if ctx["bias"] == "BULLISH" else -1
    if C.OPT_CONFIRM and spot_trend != d:
        return None  # OI says one thing, price is not moving that way yet
    strength, spot_risk = None, None
    if idx_df is not None and len(idx_df) > C.EMA_SLOW + 5:
        strength = float(adx(idx_df).iloc[-1])
        if C.OPT_CONFIRM and strength < C.OPT_MIN_ADX:
            return None  # sideways index: premium decays while you wait
        a = float(atr(idx_df).iloc[-1])
        rec = idx_df.tail(3)
        swing = float(rec["low"].min() if d > 0 else rec["high"].max())
        spot_risk = max(abs(ctx["spot"] - swing), a)
    wall = ctx["resistance"] if d > 0 else ctx["support"]
    room = (wall - ctx["spot"]) / ctx["spot"] * 100 * d
    if 0 < room < C.OPT_MIN_ROOM_PCT:
        return None  # big OI wall just ahead: little room to run
    pick = S.best_buy(df, "CE" if d > 0 else "PE", ctx["spot"], strict=True)
    if not pick or pick["ltp"] <= 0:
        return None
    ltp = pick["ltp"]
    if spot_risk:
        sl, tgt, risk_pct = premium_levels(ltp, pick["delta"], spot_risk, C.OPT_RR,
                                           C.OPT_SL_MIN_PCT, C.OPT_SL_PCT)
        plan = f"SL {risk_pct}% (index stop {round(ctx['spot'] - d * spot_risk, 1)}) · 1:{C.OPT_RR:g}"
    else:
        sl, tgt = round(ltp * (1 - C.OPT_SL_PCT / 100), 2), round(ltp * (1 + C.OPT_TARGET_PCT / 100), 2)
        plan = f"SL {C.OPT_SL_PCT:g}% · target {C.OPT_TARGET_PCT:g}%"
    extra = f"ADX {strength:.0f} · " if strength is not None else ""
    room_txt = f"room to OI wall {wall}: {room:.2f}%" if room > 0 else f"spot beyond OI wall {wall}"
    return {**ctx, "kind": "OPT_BUY", "direction": d, "fav": 1, "mode": "price", "pick": pick,
            "entry": ltp, "sl": sl, "target": tgt, "watch_key": pick["key"], "plan": plan,
            "note": extra + room_txt, "intraday": True,
            "key": f"OB-{ctx['symbol']}-{pick['strike']}{pick['opt']}"}


def option_swing_setup(cdf, sig, expiry):
    """Positional option buy for an index daily setup: slightly ITM, farther expiry."""
    d = sig["direction"]
    pick = S.best_buy(cdf, "CE" if d > 0 else "PE", sig["spot"], strict=True,
                      dmin=C.OPT_SWING_DELTA_MIN, dmax=C.OPT_SWING_DELTA_MAX)
    if not pick or pick["ltp"] <= 0:
        return None
    ltp = pick["ltp"]
    sl, tgt, risk_pct = premium_levels(ltp, pick["delta"], sig["spot_risk"], C.OPT_SWING_RR,
                                       C.OPT_SWING_SL_MIN_PCT, C.OPT_SWING_SL_MAX_PCT)
    return {**sig, "kind": "OPT_SWING", "fav": 1, "mode": "price", "pick": pick,
            "expiry": expiry, "entry": ltp, "sl": sl, "target": tgt, "risk_pct": risk_pct,
            "watch_key": pick["key"], "intraday": False,
            "key": f"OSW-{sig['symbol']}-{d}-{sig['date']}"}


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
