# Upstox Alert Bot

Telegram alerts for NSE stocks and NIFTY/BANKNIFTY options. **Alerts only – no orders are placed.**

| Module | Timeframe | Logic |
|---|---|---|
| Swing | Daily, scanned 08:30 (yesterday's close) + 15:40 (today's close) | 20-day breakout/breakdown + volume ≥1.5× + 50/200 DMA trend. SL 2×ATR, target 1:2 |
| Intraday | 5-min | EMA 9/21 cross + volume ≥1.5× + VWAP side. SL = wider of candle or 1×ATR, target 1:2 |
| Scalping | 1-min, live WebSocket | EMA 5/13 cross + VWAP/volume (stocks) or RSI (index). SL 1×ATR, target 1.5×ATR. Index scalps include the best option strike |
| Options buy | Chain, every 120s (`OPTIONS_SCAN_SEC`) | PCR + OI change → bias. Best strike: delta 0.40–0.65, most liquid, tight bid-ask |
| Options sell | Chain, 09:45–14:00 | Bullish → Bull Put Spread, Bearish → Bear Call Spread, Neutral → Iron Condor. Short strike: OTM delta 0.15–0.30 at/beyond OI wall, hedged 4 strikes away |
| Exits | Every 5s | SL hit, move SL to cost at +1R, target (book 50%), trailing SL, opposite signal, spread profit/SL, spot breach, 15:15 square-off |

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
   `STOCK_WATCHLIST=NSE_EQ|INE002A01018:RELIANCE` or `ENABLE_SCALP=false`.
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
- If the live feed fails, scalping automatically falls back to REST 1-min candles (60s delay).
- Option sell setups need margin; quantities/lot sizes are not calculated.
- NSE holidays (2026 list in `config.py`) are skipped. Add more with `MARKET_HOLIDAYS=YYYY-MM-DD,...`;
  run on a special weekend session (Budget day, Muhurat) with `SPECIAL_SESSIONS=YYYY-MM-DD`.
- Find instrument keys in Upstox's instrument file:
  `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz`

## Disclaimer
Rule-based signals for personal/educational use, not investment advice. Paper-trade first.
