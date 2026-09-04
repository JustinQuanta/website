"""
Helpers to get a more up-to-date 'current price' for a ticker.

Behavior:
- Try to read from a dedicated 'latest_quotes' table (if you maintain intraday/quote updates).
- Else try to fetch a live quote via yfinance (fast_info or 1m history).
- Else fall back to the latest close in daily_prices (what you currently have).

Returns: (price: float or None, price_date: "YYYY-MM-DD HH:MM:SS" string or date-only, source: str)
"""
import logging
from datetime import datetime
import sqlite3

try:
    import yfinance as yf
except Exception:
    yf = None

logger = logging.getLogger(__name__)

def _get_from_latest_quotes_table(conn, stock_id):
    """
    If you maintain a real-time/intraday quotes table (e.g. latest_quotes or intraday_quotes),
    prefer that. Expected columns: last_price, price_ts (ISO), stock_id.
    """
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT last_price, price_ts FROM latest_quotes WHERE stock_id=? ORDER BY price_ts DESC LIMIT 1",
            (stock_id,)
        ).fetchone()
        if row and row["last_price"] is not None:
            ts = row["price_ts"]
            return float(row["last_price"]), ts, "latest_quotes"
    except sqlite3.OperationalError:
        # table does not exist
        pass
    except Exception as e:
        logger.debug("latest_quotes lookup failed: %s", e)
    return None, None, None

def _get_from_yfinance(ticker):
    """
    Try yfinance quick lookups. Returns (price, price_date, 'yfinance') or (None, None, None).
    Requires yfinance installed.
    """
    if yf is None:
        return None, None, None
    try:
        tk = yf.Ticker(ticker)
        # Attempt fast_info (best-effort)
        fi = getattr(tk, "fast_info", None)
        if fi:
            # fast_info may have last_price, last_price may be None for some tickers
            last = fi.get("last_price") or fi.get("lastTradePriceOnly") or fi.get("lastClose")
            if last:
                # Use utc now as timestamp if we don't have one
                return float(last), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "yfinance.fast_info"
        # Fallback: small interval history for today (1m). This may be slow.
        hist = tk.history(period="1d", interval="1m", actions=False)
        if not hist.empty:
            # get most recent close in the intraday series
            last_price = hist["Close"].iloc[-1]
            last_ts = hist.index[-1].to_pydatetime().strftime("%Y-%m-%d %H:%M:%S")
            return float(last_price), last_ts, "yfinance.history_1m"
    except Exception as e:
        logger.debug("yfinance realtime lookup failed: %s", e)
    return None, None, None

def _get_from_daily_prices(conn, stock_id):
    """
    Existing fallback to your daily_prices table.
    """
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT close, price_date FROM daily_prices WHERE stock_id = ? ORDER BY price_date DESC LIMIT 1",
            (stock_id,)
        ).fetchone()
        if row and row["close"] is not None:
            return float(row["close"]), row["price_date"], "daily_prices"
    except Exception as e:
        logger.warning("daily_prices fallback failed: %s", e)
    return None, None, None

def get_current_price(conn, stock_id=None, ticker=None, prefer_realtime=True):
    """
    Unified helper used by views. Provide either stock_id and/or ticker.
    Order:
    - If prefer_realtime and latest_quotes table exists -> use it
    - If prefer_realtime and ticker provided -> try yfinance quick quote
    - Fallback to daily_prices last close
    Returns (price, price_date, source)
    """
    # Validate connection
    if conn is None:
        raise ValueError("Database connection is required")

    # Try latest_quotes (your intraday table) first if stock_id known
    if prefer_realtime and stock_id is not None:
        p, ts, src = _get_from_latest_quotes_table(conn, stock_id)
        if p is not None:
            return p, ts, src

    # If ticker provided, try yfinance (or could be replaced with IEX/Finnhub)
    if prefer_realtime and ticker:
        p, ts, src = _get_from_yfinance(ticker)
        if p is not None:
            return p, ts, src

    # Fallback to daily_prices
    p, ts, src = _get_from_daily_prices(conn, stock_id)
    return p, ts, src