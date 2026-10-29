"""Settings come from environment variables: GitHub Secrets for keys, and an optional
repo Variable BOT_SETTINGS (one KEY=VALUE per line) for everything else."""
import os

# GitHub passes unset secrets/vars as empty strings -> treat them as "not set"
for _k in [k for k, v in os.environ.items() if not v.strip()]:
    del os.environ[_k]
for _line in os.getenv("BOT_SETTINGS", "").splitlines():
    _k, _, _v = _line.partition("=")
    if _k.strip() and not _k.strip().startswith("#") and _v.strip():
        os.environ.setdefault(_k.strip(), _v.strip())


def _b(name, default="true"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name, default):
    return float(os.getenv(name, str(default)))


def _i(name, default):
    return int(os.getenv(name, str(default)))


UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "").strip()
UPSTOX_SECRET = os.getenv("UPSTOX_SECRET", "").strip()
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip()  # optional; daily login via Telegram link
# Must match the Redirect URL in your Upstox developer app exactly
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1/").strip()
# GitHub jobs are capped at 6h, so each run stops after this and starts a fresh one
MAX_RUNTIME_MIN = int(os.getenv("MAX_RUNTIME_MIN", "330"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Watchlists: "INSTRUMENT_KEY:NAME,INSTRUMENT_KEY:NAME"
STOCK_WATCHLIST = os.getenv(
    "STOCK_WATCHLIST",
    "NSE_EQ|INE002A01018:RELIANCE,NSE_EQ|INE040A01034:HDFCBANK,"
    "NSE_EQ|INE009A01021:INFY,NSE_EQ|INE467B01029:TCS,NSE_EQ|INE090A01021:ICICIBANK")
INDEX_WATCHLIST = os.getenv("INDEX_WATCHLIST",
                            "NSE_INDEX|Nifty 50:NIFTY,NSE_INDEX|Nifty Bank:BANKNIFTY")
SWING_WATCHLIST = os.getenv("SWING_WATCHLIST", "") or STOCK_WATCHLIST

# Modules on/off
ENABLE_INTRADAY = _b("ENABLE_INTRADAY")
ENABLE_SCALP = _b("ENABLE_SCALP")
ENABLE_SWING = _b("ENABLE_SWING")
ENABLE_OPTIONS_BUY = _b("ENABLE_OPTIONS_BUY")
ENABLE_OPTIONS_SELL = _b("ENABLE_OPTIONS_SELL")
ENABLE_EXIT_ALERTS = _b("ENABLE_EXIT_ALERTS")

# Timings (IST, weekdays)
ACTIVE_START = os.getenv("ACTIVE_START", "08:00")
ACTIVE_END = os.getenv("ACTIVE_END", "17:00")
SWING_SCAN_TIME = os.getenv("SWING_SCAN_TIME", "08:30")      # pre-market: uses yesterday's close
SWING_EOD_TIME = os.getenv("SWING_EOD_TIME", "15:40")        # after close: confirms on today's close ("" = off)
MARKET_OPEN = os.getenv("MARKET_OPEN", "09:15")
MARKET_CLOSE = os.getenv("MARKET_CLOSE", "15:30")
ENTRY_START = os.getenv("ENTRY_START", "09:20")
NO_NEW_ENTRY_AFTER = os.getenv("NO_NEW_ENTRY_AFTER", "14:45")
SELL_START = os.getenv("SELL_START", "09:45")
SELL_END = os.getenv("SELL_END", "14:00")
SQUARE_OFF_TIME = os.getenv("SQUARE_OFF_TIME", "15:15")
SCAN_INTERVAL_SEC = _i("SCAN_INTERVAL_SEC", 60)   # intraday 5m + REST scalp fallback
OPTIONS_SCAN_SEC = _i("OPTIONS_SCAN_SEC", 120)   # option chain refresh (OI changes slowly)
FAST_LOOP_SEC = _i("FAST_LOOP_SEC", 5)

# Intraday (5-min)
CANDLE_MINUTES = _i("CANDLE_MINUTES", 5)
EMA_FAST = _i("EMA_FAST", 9)
EMA_SLOW = _i("EMA_SLOW", 21)
VOLUME_MULTIPLIER = _f("VOLUME_MULTIPLIER", 1.5)
INTRADAY_RR = _f("INTRADAY_RR", 2.0)
INTRA_MIN_RISK_PCT = _f("INTRA_MIN_RISK_PCT", 0.4)   # stop at least 0.4% away (costs need room)
INTRA_MAX_RISK_PCT = _f("INTRA_MAX_RISK_PCT", 1.5)   # skip very wide, unclear setups
INTRA_MAX_CANDLE_ATR = _f("INTRA_MAX_CANDLE_ATR", 1.8)  # skip if signal candle is overstretched
INTRA_LAST_ENTRY = os.getenv("INTRA_LAST_ENTRY", "14:00")  # 1:2 needs time before 15:15
MARKET_FILTER = _b("MARKET_FILTER")          # trade stocks only with NIFTY's 5-min trend
MARKET_INDEX = os.getenv("MARKET_INDEX", "NIFTY")

# Scalping (1-min)
SCALP_EMA_FAST = _i("SCALP_EMA_FAST", 5)
SCALP_EMA_SLOW = _i("SCALP_EMA_SLOW", 13)
SCALP_ATR_SL = _f("SCALP_ATR_SL", 1.0)
SCALP_ATR_TGT = _f("SCALP_ATR_TGT", 2.0)
SCALP_MIN_ATR_PCT = _f("SCALP_MIN_ATR_PCT", 0.15)  # stock scalps only when 1-min ATR >= 0.15%

# Swing (daily)
SWING_LOOKBACK = _i("SWING_LOOKBACK", 20)
SWING_ATR_SL = _f("SWING_ATR_SL", 2.0)
SWING_RR = _f("SWING_RR", 2.0)
# Cash shares cannot be shorted overnight in India. SELL swings are off unless you
# trade them through F&O (futures / puts): SWING_SHORTS=ON
SWING_SHORTS = _b("SWING_SHORTS", "false")
SWING_REGIME_FILTER = _b("SWING_REGIME_FILTER")  # longs only if NIFTY > 50 DMA, shorts only below
SWING_MAX_EXT_ATR = _f("SWING_MAX_EXT_ATR", 1.5)   # skip if close is > 1.5 ATR past breakout level
SWING_CLOSE_LOC = _f("SWING_CLOSE_LOC", 0.6)       # buy: close in top 40% of day's range
SWING_MIN_TURNOVER_CR = _f("SWING_MIN_TURNOVER_CR", 5)  # avg daily traded value, Rs crore
SWING_MAX_DAYS = _i("SWING_MAX_DAYS", 15)          # time exit for swings that go nowhere

# Options analysis
STRIKES_AROUND_ATM = _i("STRIKES_AROUND_ATM", 5)
PCR_BULLISH = _f("PCR_BULLISH", 1.2)
PCR_BEARISH = _f("PCR_BEARISH", 0.8)
IV_SPIKE_PCT = _f("IV_SPIKE_PCT", 15)
MIN_PREMIUM = _f("MIN_PREMIUM", 5)
MAX_SPREAD_PCT = _f("MAX_SPREAD_PCT", 3)
# Option buying: best strike = delta in this band, liquid, tight spread
BUY_DELTA_MIN = _f("BUY_DELTA_MIN", 0.40)
BUY_DELTA_MAX = _f("BUY_DELTA_MAX", 0.65)
OPT_SL_PCT = _f("OPT_SL_PCT", 25)
OPT_TARGET_PCT = _f("OPT_TARGET_PCT", 40)
OPT_BUY_START = os.getenv("OPT_BUY_START", "09:45")   # OI change at 9:20 is mostly noise
OPT_EXPIRY_CUTOFF = os.getenv("OPT_EXPIRY_CUTOFF", "13:00")  # no new buys on expiry day after this
OPT_CONFIRM = _b("OPT_CONFIRM")   # OI bias must agree with the index's 5-min price trend
# Option selling: best strike = OTM delta in this band, at/beyond OI wall, hedged
SELL_DELTA_MIN = _f("SELL_DELTA_MIN", 0.15)
SELL_DELTA_MAX = _f("SELL_DELTA_MAX", 0.30)
HEDGE_STRIKES = _i("HEDGE_STRIKES", 4)
IV_SELL_MIN = _f("IV_SELL_MIN", 11)
MIN_CREDIT_PCT = _f("MIN_CREDIT_PCT", 10)  # credit must be >= 10% of spread width
SELL_SL_PCT = _f("SELL_SL_PCT", 50)       # exit if spread value rises 50% above credit
SELL_TARGET_PCT = _f("SELL_TARGET_PCT", 50)  # book when 50% of credit captured

# NSE trading holidays (weekdays). Override/extend with MARKET_HOLIDAYS=YYYY-MM-DD,YYYY-MM-DD
# 2026 list per NSE (via Upstox/Cleartax). Update every January from the NSE circular.
NSE_HOLIDAYS_2026 = ("2026-01-26,2026-03-03,2026-03-26,2026-03-31,2026-04-03,2026-04-14,"
                     "2026-05-01,2026-05-28,2026-06-26,2026-09-14,2026-10-02,2026-10-20,"
                     "2026-11-10,2026-11-24,2026-12-25")
MARKET_HOLIDAYS = {d.strip() for d in
                   (NSE_HOLIDAYS_2026 + "," + os.getenv("MARKET_HOLIDAYS", "")).split(",") if d.strip()}
# Special weekend sessions (e.g. Budget day, Muhurat) to run on: SPECIAL_SESSIONS=2026-11-08
SPECIAL_SESSIONS = {d.strip() for d in os.getenv("SPECIAL_SESSIONS", "").split(",") if d.strip()}


def is_market_day(d):
    ds = d.isoformat()
    if ds in SPECIAL_SESSIONS:
        return True
    return d.weekday() < 5 and ds not in MARKET_HOLIDAYS


# Exit management
TRAIL_R = _f("TRAIL_R", 1.0)  # trailing distance in multiples of initial risk
STATE_DIR = os.getenv("STATE_DIR", "data")  # saved between runs by the Actions cache


# Whole-index watchlists: write NIFTY50 / NIFTY100 / NIFTY200 / NIFTY500 (or several,
# comma-separated, e.g. NIFTY50,NIFTYMIDCAP100) instead of listing stocks one by one.
# The list is downloaded from NSE each run and cached in STATE_DIR as a fallback.
INDEX_FILES = {
    "NIFTY50": "ind_nifty50list.csv", "NIFTYNEXT50": "ind_niftynext50list.csv",
    "NIFTY100": "ind_nifty100list.csv", "NIFTY200": "ind_nifty200list.csv",
    "NIFTY500": "ind_nifty500list.csv", "NIFTYMIDCAP100": "ind_niftymidcap100list.csv",
    "NIFTYSMALLCAP100": "ind_niftysmallcap100list.csv", "NIFTYBANK": "ind_niftybanklist.csv",
}
_INDEX_URLS = ("https://nsearchives.nseindia.com/content/indices/{}",
               "https://www.niftyindices.com/IndexConstituent/{}",
               "https://archives.nseindia.com/content/indices/{}")


def _index_members(name):
    import csv, io, logging
    import requests
    log = logging.getLogger("config")
    fname = INDEX_FILES[name]
    cache = os.path.join(STATE_DIR, fname)
    text = ""
    for url in _INDEX_URLS:
        try:
            r = requests.get(url.format(fname), timeout=20,
                             headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
            if r.ok and "ISIN" in r.text[:300]:
                text = r.text
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(cache, "w") as f:
                    f.write(text)
                break
        except Exception:
            pass
    if not text and os.path.exists(cache):
        log.warning("%s download failed - using saved copy", name)
        with open(cache) as f:
            text = f.read()
    if not text:
        log.warning("%s list unavailable - skipped", name)
        return []
    rows = csv.DictReader(io.StringIO(text))
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        isin, sym = r.get("ISIN Code", ""), r.get("Symbol", "")
        if isin and sym:
            out.append(f"NSE_EQ|{isin}:{sym}")
    log.info("%s: %d stocks loaded", name, len(out))
    return out


def parse_watchlist(raw):
    parts = []
    for part in raw.split(","):
        tag = part.strip().upper().replace(" ", "").replace("_", "")
        parts.extend(_index_members(tag) if tag in INDEX_FILES else [part])
    raw = ",".join(parts)
    items, seen = [], set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            key, _, name = part.rpartition(":")
            item = (key.strip(), name.strip()) if key else (name, name)
            if item[0] not in seen:
                seen.add(item[0])
                items.append(item)
    return items


def missing_vars():
    req = {"TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN, "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
           "UPSTOX_API_KEY": UPSTOX_API_KEY, "UPSTOX_SECRET": UPSTOX_SECRET}
    return [k for k, v in req.items() if not v]
