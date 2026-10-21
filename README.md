# Upstox Alert Bot

Telegram alerts for NSE stocks and NIFTY/BANKNIFTY options. **Alerts only – no orders are placed.**

| Module | Timeframe | Logic |
|---|---|---|
| Swing | Daily, scanned 08:30 | 20-day breakout/breakdown + volume ≥1.5× + 50/200 DMA trend. SL 2×ATR, target 1:2 |
| Intraday | 5-min | EMA 9/21 cross + volume ≥1.5× + VWAP side. SL = wider of candle or 1×ATR, target 1:2 |
| Scalping | 1-min, live WebSocket | EMA 5/13 cross + VWAP/volume (stocks) or RSI (index). SL 1×ATR, target 1.5×ATR. Index scalps include the best option strike |
| Options buy | Chain, every 60s | PCR + OI change → bias. Best strike: delta 0.40–0.65, most liquid, tight bid-ask |
| Options sell | Chain, 09:45–14:00 | Bullish → Bull Put Spread, Bearish → Bear Call Spread, Neutral → Iron Condor. Short strike: OTM delta 0.15–0.30 at/beyond OI wall, hedged 4 strikes away |
| Exits | Every 5s | SL hit, move SL to cost at +1R, target (book 50%), trailing SL, opposite signal, spread profit/SL, spot breach, 15:15 square-off |

Telegram commands: `/positions`, `/close N` (stop tracking a trade you didn't take), `/status`, `/help`.

## Deploy
1. Push this folder to GitHub (`.env` is git-ignored – never commit keys).
2. Railway → New Project → Deploy from GitHub repo. The Dockerfile is detected automatically.
3. Railway → Variables: add `ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (others optional, see `.env.example`).
4. **Add a Volume** (service → Settings → Volumes) mounted at `/data`. This keeps swing positions
   saved when the bot restarts after your daily token update.

## Daily token
Upstox tokens expire daily (~3:30 AM). Each morning before 8:30:
```
pip install requests
python get_token.py
```
Paste the token into Railway → `ACCESS_TOKEN`. The bot restarts and warns you on Telegram if the token is expired.

## Notes
- If the live feed fails, scalping automatically falls back to REST 1-min candles (60s delay).
- Option sell setups need margin; quantities/lot sizes are not calculated.
- NSE holidays aren't skipped (the bot just finds no new data).
- Find instrument keys in Upstox's instrument file:
  `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz`

## Disclaimer
Rule-based signals for personal/educational use, not investment advice. Paper-trade first.
