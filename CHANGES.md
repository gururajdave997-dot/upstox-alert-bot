# Tuning update – 29 Oct 2026

Why paper P&L was negative: intraday/scalp stops were 0.2–0.3% wide, but the paper engine
charged ~0.3% friction (0.1% cost + slippage, both sides) on positions sized to the full
capital. Costs were 0.6–2.5R per trade, so even good setups lost money.

## Paper engine (paper_trade.py)
- Realistic, segment-wise charges: intraday 0.02%, delivery 0.12%, options 0.07% of turnover,
  plus Rs 20/order brokerage (+GST). Options slippage 0.5% per side (was 0.05%).
- Skips any trade whose stop is < 3x round-trip cost (PAPER_MIN_R_COST).
- Swing position value capped at 20% of capital; all open stock trades capped at 100%.
- Max open per type (PAPER_MAX_OPEN), max 8 intraday-type trades/day, stop after -1.5% day.
- At +1R: book half, SL to cost + charges, trail the rest by 1R (matches exit alerts).
- Intraday trades left open overnight are squared off at the next price. Swing time exit 15 days.
- Option quantity now in real lots (lot size from Upstox contract API).
- /pnl shows average R per type and unrealised P&L of open trades.

## Signals (strategy.py)
- Intraday: stop >= 0.4% (from 3-candle swing), skip > 1.5%, no entries after 14:00,
  must agree with NIFTY 5-min trend, EMA21 must slope the same way, no spike-candle chasing.
- Scalp (stocks): only if 1-min ATR >= 0.15%, with NIFTY trend; target 2 ATR (was 1.5).
- Swing: SELL swings off by default (SWING_SHORTS=ON to allow, F&O only); NIFTY 50-DMA regime
  filter; close in top 40% of day's range; not > 1.5 ATR past breakout; RSI < 78;
  min Rs 5 cr daily turnover; no duplicate alert from the 15:40 + next 8:30 scans.
- Option buy: from 09:45; needs index price trend to agree with OI bias; skips if no liquid
  strike passes filters (no forced ATM pick); no new buys after 13:00 on expiry day.

## New commands
- /closeall SWING – stop tracking all swing positions.
