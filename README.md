# Upstox Alert Bot

Telegram alerts for NSE stocks and NIFTY/BANKNIFTY options. **Alerts only – no orders are placed.**

| Module | Timeframe | Logic |
|---|---|---|
| Swing | Daily, scanned 08:30 + 15:40 | **BREAKOUT** (20-day high on 1.5× volume) or **PULLBACK** (dip to 20 EMA in an uptrend, then reversal). Needs 50/200 DMA trend, NIFTY regime, relative strength vs NIFTY (3 months), liquidity. Target 1:2. F&O stocks also get a monthly call option alternative |
| Intraday | 5-min | **ORB** (15-min opening-range breakout, till 11:30), **PULLBACK** (dip to EMA21/VWAP, trend resumes) or **CROSS** (EMA 9/21). Filters: volume, VWAP side, ADX trend strength, 15-min trend, RSI not stretched, stronger/weaker than NIFTY today, NIFTY trend. Max 2 alerts per stock per day. Target 1:2 |
| Options buy (intraday) | Chain every 120s, 09:45–14:00 | PCR + OI change bias, confirmed by index 5-min trend with ADX ≥ 18, room before the next OI wall. Strike delta 0.40–0.65, liquid. SL/target from the index's own swing, mapped through delta (12–25% of premium, 1:2) |
| Options swing (positional) | Daily, with the swing scans | NIFTY/BANKNIFTY daily trend (close > 20 EMA > 50 DMA, ADX ≥ 18) + 20-day breakout or 20 EMA pullback. Expiry ≥ 7 days out, slightly ITM (delta 0.50–0.70). SL 20–40% of premium from the index stop, 1:2, time exit 5 days |
| Options sell | Chain, 09:45–14:00 | Bullish → Bull Put Spread, Bearish → Bear Call Spread, Neutral → Iron Condor. Short strike: OTM delta 0.15–0.30 at/beyond OI wall, hedged 4 strikes away |
| Exits | Every 5s | SL hit, move SL to cost at +1R, target (book 50%), trailing SL, opposite signal, spread profit/SL, spot breach, 15:15 square-off (intraday only), time exits |

Turn setups on/off with `INTRA_SETUPS=ORB,PULLBACK,CROSS`, `SWING_SETUPS=BREAKOUT,PULLBACK`,
`ENABLE_OPTIONS_SWING=false`, `SWING_OPTION_ALT=false`. All other settings are in `config.py`.

Telegram commands: `/login`, `/code XXXX`, `/positions`, `/close N` (stop tracking a trade you didn't take), `/status`, `/help`.

## Deploy on GitHub Actions (free, no server)
1. Create a **public** GitHub repo and push this folder. (Public = unlimited free Actions
   minutes; your keys stay hidden in Secrets. A private repo gets only 2,000 free min/month,
   and this bot uses ~9 h per trading day.)
2. **Settings → Pages** → Source: *Deploy from a branch*, branch `main`, folder `/docs`.
   Your helper page will be `https://<username>.github.io/<repo>/`.
3. **Upstox developer app** → set Redirect URL to exactly that page address.
4. **Settings → Secrets and variables → Actions → Secrets**, add:
   `UPSTOX_API_KEY`, `UPSTOX_SECRET`, `UPSTOX_REDIRECT_URI` (same URL as step 3),
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
5. Optional: under **Variables**, add `BOT_SETTINGS` with one `KEY=VALUE` per line, e.g.
   `STOCK_WATCHLIST=NSE_EQ|INE002A01018:RELIANCE` or `ENABLE_OPTIONS_SWING=false`.
6. **Actions** tab → enable workflows → *Upstox Alert Bot* → **Run workflow** to test.

It then starts by itself at ~07:00 IST on weekdays, skips NSE holidays, runs 08:00–17:00 IST,
and restarts itself once mid-day (GitHub caps a job at 6 h). Token, positions and today's alerts are carried
over in the Actions cache (token encrypted).

## Daily login
Upstox tokens expire every night. When the bot sends **🔑 Login needed**:
tap the link → log in → Telegram opens → press **Start**. Done.
No GitHub Pages? Use `https://127.0.0.1/` as the redirect URL, and after login copy the
address from the browser bar (it shows an error page – that's fine) and send it to the bot.

## Notes
- The live feed only speeds up exit alerts. If it fails, prices come from REST instead.
- Option sell setups need margin; quantities/lot sizes are not calculated.
- NSE holidays (2026 list in `config.py`) are skipped. Add more with `MARKET_HOLIDAYS=YYYY-MM-DD,...`;
  run on a special weekend session (Budget day, Muhurat) with `SPECIAL_SESSIONS=YYYY-MM-DD`.
- Find instrument keys in Upstox's instrument file:
  `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz`

## Disclaimer
Rule-based signals for personal/educational use, not investment advice. Paper-trade first.
