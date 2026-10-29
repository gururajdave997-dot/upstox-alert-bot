"""Best-strike selection for option BUYING and SELLING (CE & PE)."""
import pandas as pd
import config as C


def chain_df(chain):
    rows = []
    for it in chain:
        r = {"strike": float(it["strike_price"]), "spot": it.get("underlying_spot_price")}
        for p, k in (("ce", "call_options"), ("pe", "put_options")):
            o = it.get(k) or {}
            md, g = o.get("market_data") or {}, o.get("option_greeks") or {}
            oi = md.get("oi") or 0
            r.update({f"{p}_key": o.get("instrument_key"), f"{p}_ltp": md.get("ltp") or 0,
                      f"{p}_oi": oi, f"{p}_prev_oi": md.get("prev_oi", oi) or 0,
                      f"{p}_vol": md.get("volume") or 0, f"{p}_bid": md.get("bid_price") or 0,
                      f"{p}_ask": md.get("ask_price") or 0, f"{p}_delta": g.get("delta"),
                      f"{p}_iv": g.get("iv") or 0})
        rows.append(r)
    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)


def _cand(df, opt):
    p = opt.lower()
    c = pd.DataFrame({"strike": df["strike"], "key": df[f"{p}_key"], "ltp": df[f"{p}_ltp"],
                      "oi": df[f"{p}_oi"], "vol": df[f"{p}_vol"], "bid": df[f"{p}_bid"],
                      "ask": df[f"{p}_ask"], "iv": df[f"{p}_iv"],
                      "delta": pd.to_numeric(df[f"{p}_delta"], errors="coerce").abs()})
    ok = (c["bid"] > 0) & (c["ask"] > 0) & (c["ltp"] > 0)
    c["spread_pct"] = ((c["ask"] - c["bid"]) / c["ltp"] * 100).where(ok)
    c["liq"] = (c["oi"].rank(pct=True) + c["vol"].rank(pct=True)) / 2
    return c


def _pack(row, opt, why):
    d = row["delta"]
    return {"strike": int(row["strike"]), "opt": opt, "ltp": round(float(row["ltp"]), 2),
            "delta": None if pd.isna(d) else round(float(d), 2), "iv": round(float(row["iv"]), 1),
            "oi": int(row["oi"]), "key": row["key"], "why": why}


def _why(row):
    parts = []
    if pd.notna(row["delta"]):
        parts.append(f"Δ {row['delta']:.2f}")
    parts.append(f"liquidity top {max(1, int((1 - row['liq']) * 100))}%")
    if pd.notna(row["spread_pct"]):
        parts.append(f"bid-ask {row['spread_pct']:.1f}%")
    return ", ".join(parts)


def best_buy(df, opt, spot, strict=False):
    """Buy: delta ~0.5 (ATM/slightly ITM), high OI+volume, tight spread, premium >= min."""
    c = _cand(df, opt)
    ok = c[c["delta"].between(C.BUY_DELTA_MIN, C.BUY_DELTA_MAX) & (c["ltp"] >= C.MIN_PREMIUM)
           & (c["spread_pct"].fillna(99) <= C.MAX_SPREAD_PCT)]
    if ok.empty:
        if strict:
            return None  # no liquid strike with a sensible delta: skip the trade
        row = c.loc[(c["strike"] - spot).abs().idxmin()]
        return _pack(row, opt, "ATM (no strike passed delta/liquidity filters)")
    score = (1 - (ok["delta"] - 0.5).abs() / 0.15) * 0.6 + ok["liq"] * 0.4 \
        - ok["spread_pct"].fillna(0) / 100
    row = ok.loc[score.idxmax()]
    return _pack(row, opt, _why(row))


def best_sell(df, opt, spot, wall):
    """Sell: OTM, delta 0.15-0.30, at/beyond the OI wall, liquid, decent premium."""
    c = _cand(df, opt)
    otm = c["strike"] > spot if opt == "CE" else c["strike"] < spot
    ok = c[otm & c["delta"].between(C.SELL_DELTA_MIN, C.SELL_DELTA_MAX)
           & (c["ltp"] >= C.MIN_PREMIUM) & (c["spread_pct"].fillna(99) <= C.MAX_SPREAD_PCT)]
    if ok.empty:
        w = c[otm & (c["strike"] == wall) & (c["ltp"] >= C.MIN_PREMIUM)]
        return _pack(w.iloc[0], opt, "OI wall strike (delta filter not met)") if len(w) else None
    beyond = (ok["strike"] >= wall) if opt == "CE" else (ok["strike"] <= wall)
    score = ok["liq"] * 0.4 + beyond * 0.3 + (1 - (ok["delta"] - 0.22).abs() / 0.08).clip(0) * 0.3
    row = ok.loc[score.idxmax()]
    return _pack(row, opt, _why(row) + (", beyond OI wall" if beyond[row.name] else ""))


def hedge(df, opt, strike):
    """Protective leg HEDGE_STRIKES further OTM."""
    c = _cand(df, opt)
    i = int(c.index[c["strike"] == strike][0])
    j = min(len(c) - 1, i + C.HEDGE_STRIKES) if opt == "CE" else max(0, i - C.HEDGE_STRIKES)
    return None if j == i else _pack(c.loc[j], opt, "hedge")
