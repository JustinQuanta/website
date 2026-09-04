import yfinance as yf
import sqlite3
import pandas as pd
import numpy as np
import requests
from datetime import datetime
import os

def get_financial_db_conn():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'instance', 'financial_data.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_stock_id(conn, ticker_symbol):
    cursor = conn.execute("SELECT id FROM stocks WHERE ticker = ?", (ticker_symbol.upper(),))
    result = cursor.fetchone()
    return result['id'] if result else None

def get_exchange_rate_for_ticker(conn, ticker):
    try:
        stock = yf.Ticker(ticker)
        financial_currency = stock.info.get("financialCurrency", "USD")
        if financial_currency == "USD" or financial_currency is None:
            return 1.0
        fx_data = requests.get("https://api.exchangerate-api.com/v4/latest/USD").json()
        exchange_rate = fx_data["rates"].get(financial_currency)
        if exchange_rate:
            return float(exchange_rate)
        else:
            return 1.0
    except Exception as e:
        print(f"[WARNING] Could not fetch FX rate for {ticker}: {e}")
        return 1.0

def update_stock_info(conn, ticker_obj):
    info = ticker_obj.info
    symbol = info.get('symbol')
    sql = ''' INSERT INTO stocks (ticker, company_name, currency, exchange, exchange_timezone, country, sector, industry, last_updated)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(ticker) DO UPDATE SET
                company_name=excluded.company_name, currency=excluded.currency, exchange=excluded.exchange,
                exchange_timezone=excluded.exchange_timezone, country=excluded.country, sector=excluded.sector,
                industry=excluded.industry, last_updated=excluded.last_updated; '''
    params = (
        symbol, info.get('longName'), info.get('financialCurrency'), info.get('exchange'),
        info.get('exchangeTimezoneName'), info.get('country'), info.get('sector'),
        info.get('industry'), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        return get_stock_id(conn, symbol)
    except sqlite3.Error as e:
        print(f"  [ERROR] Failed to upsert stock info for {symbol}: {e}")
        return None

def upsert_analyst_growth_estimate(conn, stock_id, estimate_date, growth_next_year, growth_next_5_years, equity_growth):
    # Sort and assign values to respective scenarios
    numbers = [growth_next_year, growth_next_5_years, equity_growth]
    growth_conservative = min(numbers)
    growth_optimistic = max(numbers)
    growth_moderate = sum(numbers) / len(numbers)  # mean (or use median if preferred)

    sql = """
    INSERT INTO analyst_growth_estimates (stock_id, estimate_date, growth_conservative, growth_moderate, growth_optimistic)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(stock_id, estimate_date) DO UPDATE SET
        growth_conservative=excluded.growth_conservative,
        growth_moderate=excluded.growth_moderate,
        growth_optimistic=excluded.growth_optimistic
    """
    conn.execute(sql, (stock_id, estimate_date, growth_conservative, growth_moderate, growth_optimistic))
    conn.commit()

def get_latest_analyst_growth_estimate(conn, stock_id):
    row = conn.execute(
        "SELECT growth_conservative, growth_moderate, growth_optimistic FROM analyst_growth_estimates WHERE stock_id=? ORDER BY estimate_date DESC LIMIT 1",
        (stock_id,)
    ).fetchone()
    if row:
        return {
            "conservative": row["growth_conservative"],
            "moderate": row["growth_moderate"],
            "optimistic": row["growth_optimistic"]
        }
    return {"conservative": None, "moderate": None, "optimistic": None}

def get_latest_cashflow_db(conn, stock_id):
    # Try free cash flow, then net income
    cursor = conn.cursor()
    # Get the latest report year
    row = cursor.execute(
        """
        SELECT report_year, free_cash_flow, net_income FROM annual_financials 
        WHERE stock_id=? AND (free_cash_flow IS NOT NULL OR net_income IS NOT NULL)
        ORDER BY report_year DESC LIMIT 1
        """,
        (stock_id,)
    ).fetchone()
    if row:
        fcf = row['free_cash_flow']
        if fcf is not None and fcf != 0:
            return float(fcf)
        ni = row['net_income']
        if ni is not None and ni != 0:
            return float(ni)
    print("[WARNING] Could not retrieve Free Cash Flow or Net Income from DB.")
    return 0.0

def get_shares_outstanding_db(conn, stock_id):
    cursor = conn.cursor()
    # Try daily_prices first (should be most recent point-in-time)
    row = cursor.execute(
        "SELECT shares_outstanding FROM daily_prices WHERE stock_id=? ORDER BY price_date DESC LIMIT 1",
        (stock_id,)
    ).fetchone()
    if row and row['shares_outstanding'] is not None and row['shares_outstanding'] != 0:
        return int(row['shares_outstanding'])
    # Fallback: try annual_financials if schema supports it
    row = cursor.execute(
        "SELECT shares_outstanding FROM annual_financials WHERE stock_id=? ORDER BY report_year DESC LIMIT 1",
        (stock_id,)
    ).fetchone()
    if row and row['shares_outstanding'] is not None and row['shares_outstanding'] != 0:
        return int(row['shares_outstanding'])
    print("[WARNING] Could not retrieve Shares Outstanding from DB.")
    return 0

def get_equity_growth_db(conn, stock_id):
    cursor = conn.cursor()
    # Get Stockholders Equity for the last several years
    rows = cursor.execute(
        "SELECT report_year, total_stockholder_equity FROM annual_financials WHERE stock_id=? AND total_stockholder_equity IS NOT NULL ORDER BY report_year DESC LIMIT 5",
        (stock_id,)
    ).fetchall()
    equities = [float(row['total_stockholder_equity']) for row in rows if row['total_stockholder_equity'] is not None and row['total_stockholder_equity'] > 0]
    if len(equities) > 1:
        try:
            # Calculate CAGR
            n = len(equities) - 1
            equity_growth = (equities[0] / equities[-1])**(1 / n) - 1
            return equity_growth
        except Exception as e:
            print(f"[WARNING] Error calculating equity growth: {e}")
            return 0.0
    return 0.0

def perform_finite_horizon_dcf(latest_cf, growth_rate, discount_rate, projection_years, margin_of_safety):
    total_pv = 0.0
    for year in range(1, projection_years + 1):
        cf_this_year = latest_cf * ((1 + growth_rate/100.0) ** year)
        discounted_val = cf_this_year / ((1 + discount_rate) ** year)
        total_pv += discounted_val
    # Apply margin of safety
    total_pv_adjusted = total_pv * (1 - margin_of_safety)
    return total_pv_adjusted

def persist_daily_finite_iv(conn, stock_id, price_date, ivs, projection_years, discount_rate, margin_of_safety):
    """
    Persists finite horizon IVs for a stock on a given date.
    ivs: dict with keys 'conservative', 'moderate', 'optimistic'
    """
    sql = """
    INSERT INTO daily_finite_iv (
        stock_id, price_date,
        iv_conservative, iv_moderate, iv_optimistic,
        projection_years, discount_rate, margin_of_safety
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_id, price_date) DO UPDATE SET
        iv_conservative=excluded.iv_conservative,
        iv_moderate=excluded.iv_moderate,
        iv_optimistic=excluded.iv_optimistic,
        projection_years=excluded.projection_years,
        discount_rate=excluded.discount_rate,
        margin_of_safety=excluded.margin_of_safety
    """
    conn.execute(sql, (
        stock_id, price_date,
        ivs.get('conservative', 0.0),
        ivs.get('moderate', 0.0),
        ivs.get('optimistic', 0.0),
        projection_years, discount_rate, margin_of_safety
    ))
    conn.commit()

def process_one_ticker_finite_horizon(conn, ticker_symbol, projection_years=10, discount_rate=0.075, margin_of_safety=0.20):
    stock_id = get_stock_id(conn, ticker_symbol)
    if not stock_id:
        print(f"Could not find stock_id for {ticker_symbol}")
        return

    try:
        ticker_obj = yf.Ticker(ticker_symbol)
        growth_estimates = ticker_obj.get_growth_estimates()
        growth_next_year = float(growth_estimates['stockTrend'].iloc[2])*100
        growth_next_5_years = float(growth_estimates['stockTrend'].iloc[3])*100
    except Exception:
        growth_next_year = 0.0
        growth_next_5_years = 0.0

    equity_growth = get_equity_growth_db(conn, stock_id)*100
    estimate_date = datetime.utcnow().strftime('%Y-%m-%d')
    upsert_analyst_growth_estimate(conn, stock_id, estimate_date, growth_next_year, growth_next_5_years, equity_growth)

    # Now use the scenario growth rates from the table
    growth_rates = get_latest_analyst_growth_estimate(conn, stock_id)

    latest_cf = get_latest_cashflow_db(conn, stock_id)
    shares = get_shares_outstanding_db(conn, stock_id)
    rate = get_exchange_rate_for_ticker(conn, ticker_symbol)

    scenarios = {
        'conservative': growth_rates["conservative"],
        'moderate': growth_rates["moderate"],
        'optimistic': growth_rates["optimistic"]
    }
    result = {}
    for name, growth_rate in scenarios.items():
        total_pv = perform_finite_horizon_dcf(latest_cf, growth_rate, discount_rate, projection_years, margin_of_safety)
        per_share_value = total_pv / (shares*rate) if shares else 0.0
        result[name] = per_share_value
        
    price_date = datetime.utcnow().strftime('%Y-%m-%d')
    persist_daily_finite_iv(
        conn,
        stock_id,
        price_date,
        result,
        projection_years,
        discount_rate,
        margin_of_safety
    )

    print(f"Ticker {ticker_symbol} intrinsic values (finite horizon): {result}")
    return result

def run_data_collection_finite_horizon(db_path, tickers, projection_years=10, discount_rate=0.075, margin_of_safety=0.20):
    conn = get_financial_db_conn()
    for ticker in tickers:
        process_one_ticker_finite_horizon(conn, ticker, projection_years, discount_rate, margin_of_safety)
    conn.close()