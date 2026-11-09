# Update – 9 Nov 2026: scalping removed, intraday / swing / options strengthened

## Removed
- Scalping (1-min) module, its settings (`ENABLE_SCALP`, `SCALP_*`) and paper-trade type.
  The live feed now only caches prices for faster exits (`ENABLE_LIVE_FEED`, lighter `ltpc` mode).
  An old `ENABLE_SCALP` line in BOT_SETTINGS is simply ignored.

## Intraday (stocks, 5-min)
- Three setups instead of one: ORB (15-min opening range breakout, first fresh close, till 11:30,
  range ≤ 1.2%), PULLBACK (trend resumes after a dip to EMA21/VWAP), CROSS (EMA 9/21, as before).
- New filters: ADX ≥ 18 (no chop), 15-min trend must agree, RSI not stretched (≤ 72 / ≥ 28),
  relative strength vs NIFTY today, max 2 alerts per stock per day.
- Alerts now show the setup, reason, ADX, RSI and RS.

## Swing (stocks, daily)
- New PULLBACK setup: uptrend, dip to 20 EMA within 3 days with RSI cooling to 38–62, then a
  close above yesterday's high. Stop under the 5-day low, kept between 1 and 2.5 ATR.
- Relative strength filter: stock must beat NIFTY over 63 days (buy leaders, not laggards).
- Alert shows distance from 52-week high. F&O stocks get a monthly CE alternative (delta 0.50–0.70).

## Options intraday buy
- Index 5-min ADX ≥ 18 required, entries 09:45–14:00 only (theta), skips if an OI wall is
  within 0.25% ahead.
- SL/target come from the index's own swing stop mapped through delta (12–25% of premium, 1:2)
  instead of a flat 25% / 40%.

## Options swing (new, positional)
- NIFTY/BANKNIFTY daily trend + 20-day breakout or 20 EMA pullback, ADX ≥ 18 → buy a slightly
  ITM option (delta 0.50–0.70) in the first expiry ≥ 7 days away. SL 20–40% of premium, 1:2,
  exit after 5 days. Tracked with exit alerts and paper-traded as OPT_SWING.
- Command: `/closeall OPT_SWING`.
