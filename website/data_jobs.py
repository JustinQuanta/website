import yfinance as yf
import sqlite3
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

def is_recent(date_str, days=7):
    """Returns True if date_str is within last N days."""
    try:
        return (datetime.utcnow() - datetime.strptime(date_str, "%Y-%m-%d")).days < days
    except Exception:
        return False

# Financials: Only skip if ALL key fields present and nonzero
def needs_update_financials(conn, stock_id, report_year, table_name='annual_financials'):
    row = conn.execute(
        f"SELECT total_revenue, net_income, interest_expense, pretax_income, tax_provision FROM {table_name} WHERE stock_id=? AND report_year=?",
        (stock_id, report_year)
    ).fetchone()
    # Update (return True) if any key field is missing/zero/None
    if not row:
        return True
    return not all(row[k] not in (None, 0) for k in row.keys())

# Prices: Only skip if ALL key fields present and nonzero
def needs_update_prices(conn, stock_id, date):
    row = conn.execute(
        "SELECT close, shares_outstanding, market_cap, beta, total_debt FROM daily_prices WHERE stock_id=? AND price_date=?",
        (stock_id, date)
    ).fetchone()
    if not row:
        return True
    return not all(row[k] not in (None, 0) for k in row.keys())

# WACC: Only skip if present and nonzero
def needs_update_wacc(conn, stock_id, date):
    row = conn.execute(
        "SELECT wacc_value FROM daily_wacc WHERE stock_id=? AND price_date=?",
        (stock_id, date)
    ).fetchone()
    if not row:
        return True
    return row['wacc_value'] in (None, 0)

# IV: Only skip if ALL key fields present and nonzero
def needs_update_iv(conn, stock_id, date):
    row = conn.execute(
        "SELECT iv_conservative, iv_moderate, iv_optimistic FROM daily_iv WHERE stock_id=? AND price_date=?",
        (stock_id, date)
    ).fetchone()
    if not row:
        return True
    return not all(row[k] not in (None, 0) for k in row.keys())

def get_financial_db_conn():
    """
    Always connects to financial_data.db inside the instance folder.
    """
    db_path = os.path.join(os.path.dirname(__file__), '..', 'instance', 'financial_data.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# helper to fetch USD→target currency
def get_exchange_rate_for_ticker(conn, ticker):
    try:
        # Get the financial reporting currency using yfinance
        stock = yf.Ticker(ticker)
        financial_currency = stock.info.get("financialCurrency", "USD")

        if financial_currency == "USD" or financial_currency is None:
            return 1.0

        # Fetch exchange rate from USD to the financial currency
        fx_data = requests.get("https://api.exchangerate-api.com/v4/latest/USD").json()
        exchange_rate = fx_data["rates"].get(financial_currency)

        if exchange_rate:
            return float(exchange_rate)
        else:
            return 1.0  # fallback if currency not found
    except Exception as e:
        print(f"[WARNING] Could not fetch FX rate for {ticker}: {e}")
        return 1.0


def _get_growth_scenarios(cur, stock_id, year):
    def find_smallest(a, b, c): return min(a, b, c)
    def find_middle(a, b, c): return sorted([a, b, c])[1]
    def find_largest(a, b, c): return max(a, b, c)
    hist_rows = cur.execute("""
      SELECT estimate_date, value
        FROM revenue_estimates
       WHERE stock_id=? 
         AND estimate_type LIKE 'hist_growth_%'
         AND value <> 0
    """, (stock_id,)).fetchall()
    hist_vals = [
      float(r['value'])
      for r in hist_rows
      if int(r['estimate_date']) <= year and float(r['value']) != 0.0
    ]
    next_year = year + 1
    row_cy = cur.execute("""
      SELECT value FROM revenue_estimates
       WHERE stock_id=? AND estimate_type=?
    """, (stock_id, f"hist_growth_{year-1}_{year}")).fetchone()
    row_ny = cur.execute("""
      SELECT value FROM revenue_estimates
       WHERE stock_id=? AND estimate_type=?
    """, (stock_id, f"hist_growth_{year}_{next_year}")).fetchone()
    est_cy = float(row_cy['value']) if row_cy else 0.0
    est_ny = float(row_ny['value']) if row_ny else 0.0
    all_vals = hist_vals + [est_ny]
    avg_hist = float(np.nanmean(all_vals))
    lo = find_smallest(est_cy, est_ny, avg_hist)
    mean = find_middle(est_cy, est_ny, avg_hist)
    hi = find_largest(est_cy, est_ny, avg_hist)
    return lo, mean, hi

def _get_fcfe_scenarios(cur, stock_id, year):
    rows = cur.execute("""
        SELECT report_year, fcfe_ratio
          FROM annual_ratios
         WHERE stock_id=? AND fcfe_ratio <> 0
         ORDER BY report_year
    """, (stock_id,)).fetchall()
    ratios = [float(r['fcfe_ratio']) for r in rows if r['fcfe_ratio'] is not None and r['fcfe_ratio'] != 0.0]
    if not ratios:
        return 0.0, 0.0, 0.0
    lo = min(ratios)
    hi = max(ratios)
    mean = np.nanmean(ratios)
    return lo, mean, hi

def _get_margin_scenarios(cur, stock_id, year):
    rows = cur.execute("""
        SELECT report_year, ni_margin
          FROM annual_ratios
         WHERE stock_id=? AND ni_margin <> 0
         ORDER BY report_year
    """, (stock_id,)).fetchall()
    margins = [float(r['ni_margin']) for r in rows if r['ni_margin'] is not None and r['ni_margin'] != 0.0]
    if not margins:
        return 0.0, 0.0, 0.0
    lo = min(margins)
    hi = max(margins)
    mean = np.nanmean(margins)
    return lo, mean, hi

def persist_daily_wacc(db_conn, stock_id):
    cursor = db_conn.cursor()
    row_t = cursor.execute("SELECT ticker FROM stocks WHERE id = ?", (stock_id,)).fetchone()
    symbol = row_t['ticker']
    ticker_obj = yf.Ticker(symbol)
    rows = cursor.execute("""
        SELECT price_date, beta, market_cap, total_debt
          FROM daily_prices
         WHERE stock_id = ?
         ORDER BY price_date
    """, (stock_id,)).fetchall()
    def get_latest_annual():
        r = cursor.execute("""
            SELECT interest_expense, other_interest_expense,
                   pretax_income, tax_provision,
                   long_term_debt, long_term_debt_and_capital_lease_obligation,
                   current_debt, other_current_borrowings
            FROM annual_financials
           WHERE stock_id = ?
           ORDER BY report_year DESC
           LIMIT 1
        """, (stock_id,)).fetchone()
        return dict(r) if r else {}
    for r in rows:
        date_str = r['price_date']
        if not is_recent(date_str) and not needs_update_wacc(db_conn, stock_id, date_str):
            continue
        beta = float(r['beta'] or 1.0)
        mcap = float(r['market_cap'] or 0.0)
        debt_day = float(r['total_debt'] or 0.0)
        rf_r = cursor.execute("""
            SELECT data_value FROM daily_market_data
             WHERE data_name='risk_free_rate_tnx'
               AND data_date <= ?
             ORDER BY data_date DESC LIMIT 1
        """, (date_str,)).fetchone()
        rfr = float(rf_r['data_value']) if rf_r else 0.035
        cost_of_equity = rfr + beta * (0.10 - rfr)
        ann = get_latest_annual()
        tp = float(ann.get('tax_provision') or 0.0)
        pi = float(ann.get('pretax_income') or 0.0)
        if tp == 0 or pi == 0:
            try:
                tp = ticker_obj.financials.loc['Tax Provision'].dropna().iloc[0]
                pi = ticker_obj.financials.loc['Pretax Income'].dropna().iloc[0]
            except Exception:
                pass
        tax_rate = tp / pi if pi else 0.21
        ie = float(ann.get('interest_expense') or ann.get('other_interest_expense') or 0)
        ltd = float(ann.get('long_term_debt') or ann.get('long_term_debt_and_capital_lease_obligation') or 0)
        std = float(ann.get('other_current_borrowings') or ann.get('current_debt') or 0)
        if ie == 0:
            try:
                ie = ticker_obj.financials.loc['Interest Expense'].dropna().iloc[0]
            except:
                pass
        if (ltd + std) == 0:
            bs = ticker_obj.balance_sheet
            try:
                ltd = bs.loc['Long Term Debt'].dropna().iloc[0]
            except:
                try:
                    ltd = bs.loc['Long Term Debt And Capital Lease Obligation'].dropna().iloc[0]
                except:
                    ltd = 0
            try:
                std = bs.loc['Other Current Borrowings'].dropna().iloc[0]
            except:
                std = bs.loc['Current Debt'].dropna().iloc[0] if 'Current Debt' in bs.index else 0
        debt_ann = ltd + std
        cost_of_debt = (ie / debt_ann) * (1 - tax_rate) if (ie and debt_ann) else 0.0
        cap_sum = mcap + debt_day
        w_e = mcap / cap_sum if cap_sum else 1.0
        w_d = debt_day / cap_sum if cap_sum else 0.0
        wacc = w_e * cost_of_equity + w_d * cost_of_debt
        cursor.execute("""
            INSERT INTO daily_wacc (stock_id, price_date, wacc_value)
            VALUES (?, ?, ?)
            ON CONFLICT(stock_id, price_date) DO UPDATE SET
              wacc_value = excluded.wacc_value;
        """, (stock_id, date_str, wacc))
    db_conn.commit()
    print(f"  -> Persisted daily WACC for stock_id={stock_id}")


def persist_daily_iv(db_conn, stock_id):
    cur = db_conn.cursor()
    def get_revs(Y):
        row1 = cur.execute(
            "SELECT total_revenue FROM annual_financials WHERE stock_id=? AND report_year=?",
            (stock_id, Y)
        ).fetchone()
        r1 = float(row1['total_revenue']) if row1 and row1['total_revenue'] is not None else 0.0
        row2 = cur.execute(
            "SELECT total_revenue FROM annual_financials WHERE stock_id=? AND report_year=?",
            (stock_id, Y+1)
        ).fetchone()
        r2 = float(row2['total_revenue']) if row2 and row2['total_revenue'] is not None else 0.0
        return r1, r2
    rows = cur.execute("""
        SELECT p.price_date, p.shares_outstanding, w.wacc_value
          FROM daily_prices p
          JOIN daily_wacc w USING(stock_id, price_date)
         WHERE p.stock_id=?
         ORDER BY p.price_date
    """, (stock_id,)).fetchall()
    sym = cur.execute("SELECT ticker FROM stocks WHERE id=?", (stock_id,)).fetchone()["ticker"]
    rate = get_exchange_rate_for_ticker(db_conn, sym)
    upsert = """
        INSERT INTO daily_iv (
          stock_id, price_date,
          iv_conservative, iv_moderate, iv_optimistic,
          iv_conservative_fixed, iv_moderate_fixed, iv_optimistic_fixed
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_id, price_date) DO UPDATE SET
          iv_conservative       = excluded.iv_conservative,
          iv_moderate           = excluded.iv_moderate,
          iv_optimistic         = excluded.iv_optimistic,
          iv_conservative_fixed = excluded.iv_conservative_fixed,
          iv_moderate_fixed     = excluded.iv_moderate_fixed,
          iv_optimistic_fixed   = excluded.iv_optimistic_fixed;
    """
    def dcf_fcfe(r1, r2, g_pct, m_pct, f_pct, disc):
        g = g_pct/100.0; m = m_pct/100.0; f = f_pct/100.0
        fc1 = r1 * m * f
        fc2 = r2 * m * f
        fc3 = fc2 * (1+g)
        fc4 = fc3 * (1+g)
        df = [(1+disc)**i for i in (1,2,3,4)]
        pv_front = fc1/df[0] + fc2/df[1] + fc3/df[2]
        perp = 0.025
        tv = (fc4*(1+perp))/(disc-perp) if disc>perp else fc4
        pv_term = (fc4 + tv)/df[3]
        return pv_front + pv_term
    for price_date, shares, wacc in rows:
        if not is_recent(price_date) and not needs_update_iv(db_conn, stock_id, price_date):
            continue
        Y = int(price_date[:4])
        rev1, rev2 = get_revs(Y)
        g_lo, g_md, g_hi = _get_growth_scenarios(cur, stock_id, Y)
        m_lo, m_md, m_hi = _get_margin_scenarios(cur, stock_id, Y)
        f_lo, f_md, f_hi = _get_fcfe_scenarios(cur, stock_id, Y)
        FIXED_RATE = 0.075
        iv_c = dcf_fcfe(rev1, rev2, g_lo, m_lo, f_lo, wacc)
        iv_m = dcf_fcfe(rev1, rev2, g_md, m_md, f_md, wacc)
        iv_o = dcf_fcfe(rev1, rev2, g_hi, m_hi, f_hi, wacc)
        iv_cf = dcf_fcfe(rev1, rev2, g_lo, m_lo, f_lo, FIXED_RATE)
        iv_mf = dcf_fcfe(rev1, rev2, g_md, m_md, f_md, FIXED_RATE)
        iv_of = dcf_fcfe(rev1, rev2, g_hi, m_hi, f_hi, FIXED_RATE)
        if shares and shares > 0:
            iv_c  = iv_c  / (shares * rate)
            iv_m  = iv_m  / (shares * rate)
            iv_o  = iv_o  / (shares * rate)
            iv_cf = iv_cf / (shares * rate)
            iv_mf = iv_mf / (shares * rate)
            iv_of = iv_of / (shares * rate)
        else:
            iv_c = iv_m = iv_o = iv_cf = iv_mf = iv_of = 0.0
        cur.execute(upsert, (
            stock_id, price_date,
            iv_c, iv_m, iv_o,
            iv_cf, iv_mf, iv_of
        ))
        cur.execute("""
            INSERT INTO latest_parameters (
                stock_id, price_date,
                growth_lo, growth_md, growth_hi,
                fcfe_lo, fcfe_md, fcfe_hi,
                margin_lo, margin_md, margin_hi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, price_date) DO UPDATE SET
                growth_lo=excluded.growth_lo,
                growth_md=excluded.growth_md,
                growth_hi=excluded.growth_hi,
                fcfe_lo=excluded.fcfe_lo,
                fcfe_md=excluded.fcfe_md,
                fcfe_hi=excluded.fcfe_hi,
                margin_lo=excluded.margin_lo,
                margin_md=excluded.margin_md,
                margin_hi=excluded.margin_hi
        """, (
            stock_id, price_date,
            g_lo, g_md, g_hi,
            f_lo, f_md, f_hi,
            m_lo, m_md, m_hi
        ))
    db_conn.commit()
    print(f"  -> Persisted daily IV for stock_id={stock_id}")

def get_stock_id(conn, ticker_symbol):
    """ Helper to get a stock's ID from its ticker symbol. """
    cursor = conn.execute("SELECT id FROM stocks WHERE ticker = ?", (ticker_symbol.upper(),))
    result = cursor.fetchone()
    return result['id'] if result else None

def update_market_data(conn):
    print("\nUpdating Daily Market Data (^TNX)...")
    try:
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5y")
        if hist.empty:
            print("  [WARN] Could not get history for ^TNX.")
            return
        records_to_save = [
            (date.strftime('%Y-%m-%d'), 'risk_free_rate_tnx', row['Close'] / 100.0)
            for date, row in hist.iterrows()
        ]
        sql = ''' INSERT OR IGNORE INTO daily_market_data (data_date, data_name, data_value)
                  VALUES (?, ?, ?) '''
        cursor = conn.cursor()
        cursor.executemany(sql, records_to_save)
        conn.commit()
        print(f"  -> Synced {cursor.rowcount} new daily risk-free rate records.")
    except Exception as e:
        print(f"  [ERROR] Failed to update market data: {e}")

def update_stock_info(conn, ticker_obj):
    info = ticker_obj.info
    symbol = info.get('symbol')
    print(f"  Upserting static stock info for {symbol}...")
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

def update_historical_data(conn, ticker_obj, stock_id):
    """
    Update historical daily_prices and revenue_estimates for a given stock.
    Ensures market_cap, shares_outstanding, total_debt, and beta are as complete as possible.
    """
    print(f"  Updating historical point-in-time data for {ticker_obj.ticker}…")
    cursor = conn.cursor()

    # --- Revenue Estimates Injection ---
    try:
        rows = cursor.execute("""
            SELECT report_year, revenue_growth
              FROM annual_ratios
             WHERE stock_id=?
             ORDER BY report_year
        """, (stock_id,)).fetchall()
        growth_entries = [
            (stock_id,
             str(r['report_year']),
             f"hist_growth_{r['report_year']-1}_{r['report_year']}",
             float(r['revenue_growth'] or 0.0))
            for r in rows
        ]
        max_actual = cursor.execute("""
            SELECT MAX(report_year) AS yr
              FROM annual_financials
             WHERE stock_id=? AND net_income IS NOT NULL
        """, (stock_id,)).fetchone()['yr']
        if max_actual is not None:
            est = ticker_obj.get_revenue_estimate()
            if not est.empty and len(est) >= 4:
                cy_pct = float(est.iloc[2, 5] or 0.0) * 100
                ny_pct = float(est.iloc[3, 5] or 0.0) * 100
                growth_entries += [
                    (stock_id, str(max_actual + 1),
                     f"hist_growth_{max_actual}_{max_actual + 1}", cy_pct),
                    (stock_id, str(max_actual + 2),
                     f"hist_growth_{max_actual + 1}_{max_actual + 2}", ny_pct),
                ]
        cursor.executemany("""
            INSERT INTO revenue_estimates
              (stock_id, estimate_date, estimate_type, value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_id, estimate_date, estimate_type)
            DO UPDATE SET value=excluded.value;
        """, growth_entries)
        conn.commit()
        print(f"  → Synced {len(growth_entries)} revenue-growth rows.")
    except Exception as e:
        print(f"  [WARN] revenue_estimates injection failed: {e}")

    # --- Fetch the most recent price date ---
    last_date = cursor.execute(
        "SELECT MAX(price_date) FROM daily_prices WHERE stock_id=?", (stock_id,)
    ).fetchone()[0] or "2019-01-01"

    # --- Download historical prices ---
    hist = ticker_obj.history(start=last_date)
    if hist.empty:
        print(f"  -> No new price data to update for {ticker_obj.ticker}.")
        return

    print(f"  -> Found {len(hist)} new daily records to process.")
    hist.index = pd.to_datetime(hist.index)
    tz = ticker_obj.info.get('exchangeTimezoneName', 'UTC')
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize('UTC').tz_convert(tz)
    else:
        hist.index = hist.index.tz_convert(tz)
    hist = hist[hist.index.strftime("%Y-%m-%d") > last_date]
    if hist.empty:
        print(f"  -> After filtering out {last_date}, no new rows remain.")
        return

    # --- Shares Outstanding ---
    raw_sh = ticker_obj.get_shares_full(start=last_date)
    if raw_sh is None:
        df_sh = pd.DataFrame(columns=["shares_outstanding"])
    elif isinstance(raw_sh, pd.DataFrame):
        df_sh = raw_sh.iloc[:, [0]].rename(columns={raw_sh.columns[0]: "shares_outstanding"})
    elif isinstance(raw_sh, pd.Series):
        df_sh = raw_sh.to_frame(name="shares_outstanding")
    else:
        df_sh = pd.Series(raw_sh, name="shares_outstanding").to_frame()
    df_sh.index = pd.to_datetime(df_sh.index)
    if df_sh.index.tz is None:
        df_sh.index = df_sh.index.tz_localize('UTC').tz_convert(tz)
    else:
        df_sh.index = df_sh.index.tz_convert(tz)
    if not df_sh.empty:
        df_sh['shares_outstanding'] = df_sh['shares_outstanding'].ffill()
        # Fill any remaining nulls with latest known
        latest_shares = ticker_obj.info.get('sharesOutstanding', None)
        if df_sh['shares_outstanding'].isnull().any() and latest_shares is not None and not pd.isnull(latest_shares):
            df_sh['shares_outstanding'] = df_sh['shares_outstanding'].fillna(latest_shares)
    else:
        latest_shares = ticker_obj.info.get('sharesOutstanding', None)

    # --- Total Debt ---
    qt_bs = ticker_obj.quarterly_balance_sheet.T
    if "Total Debt" in qt_bs:
        df_debt = qt_bs[["Total Debt"]].rename(columns={"Total Debt": "total_debt"})
        df_debt.index = pd.to_datetime(df_debt.index).tz_localize('UTC').tz_convert(tz)
    else:
        df_debt = pd.DataFrame(columns=["total_debt"])

    # --- Merge price, shares, debt ---
    merged = pd.merge_asof(
        hist.sort_index(),
        df_sh.sort_index(),
        left_index=True, right_index=True,
        direction="backward"
    )
    if not df_debt.empty:
        merged = pd.merge_asof(
            merged, df_debt.sort_index(),
            left_index=True, right_index=True,
            direction="backward"
        )
    if merged['shares_outstanding'].isnull().any() and latest_shares is not None and not pd.isnull(latest_shares):
        merged['shares_outstanding'] = merged['shares_outstanding'].fillna(latest_shares)
    # Calculate market_cap as much as possible
    merged["market_cap"] = merged["Close"] * merged["shares_outstanding"]
    # Fill beta and total_debt with latest info if missing
    merged["beta"] = ticker_obj.info.get("beta", 1.0)
    merged["total_debt"] = ticker_obj.info.get("totalDebt", 0)

    # --- Prepare for database insert ---
    rows_to_insert = []
    for dt, row in merged.iterrows():
        dt_str = dt.strftime("%Y-%m-%d")
        if not is_recent(dt_str) and not needs_update_prices(conn, stock_id, dt_str):
            print(f"    Skipping price for {ticker_obj.ticker} {dt_str}: already present.")
            continue
        rows_to_insert.append((
            stock_id,
            dt_str,
            row.get("Open"), row.get("High"), row.get("Low"), row.get("Close"), row.get("Volume"),
            row.get("shares_outstanding"), row.get("market_cap"),
            row.get("beta"), row.get("total_debt")
        ))

    if rows_to_insert:
        cursor.executemany("""
          INSERT OR IGNORE INTO daily_prices
            (stock_id, price_date, open, high, low, close, volume,
             shares_outstanding, market_cap, beta, total_debt)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows_to_insert)
        conn.commit()
        print(f"  -> Inserted {cursor.rowcount} new daily_prices rows.")

    # --- Batch Backfill for Missing market_cap ---
    cursor.execute("""
      UPDATE daily_prices
         SET market_cap = close * shares_outstanding
       WHERE stock_id = ?
         AND (market_cap IS NULL OR market_cap = 0)
         AND close IS NOT NULL
         AND shares_outstanding IS NOT NULL
    """, (stock_id,))
    conn.commit()
    print(f"  -> Back-filled missing market_cap values for stock_id={stock_id}")

    # --- Print warning for any remaining missing market_cap ---
    missing_count = cursor.execute("""
      SELECT COUNT(*) FROM daily_prices
       WHERE stock_id = ?
         AND (market_cap IS NULL OR market_cap = 0)
    """, (stock_id,)).fetchone()[0]
    if missing_count > 0:
        print(f"  [WARNING] {missing_count} daily_prices rows still missing market_cap after backfill for {ticker_obj.ticker}")

    # --- Backfill for total_debt, shares_outstanding, beta ---
    current_td = ticker_obj.info.get("totalDebt", 0)
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    cursor.execute("""
      UPDATE daily_prices
         SET total_debt = ?
       WHERE stock_id = ?
         AND total_debt IS NULL
         AND price_date <= ?
    """, (current_td, stock_id, yesterday))
    conn.commit()
    print(f"  -> Back-filled total_debt={current_td} up to {yesterday}")

    if latest_shares is not None and not pd.isnull(latest_shares):
        cursor.execute("""
          UPDATE daily_prices
             SET shares_outstanding = ?
           WHERE stock_id = ?
             AND (shares_outstanding IS NULL OR shares_outstanding = 0)
             AND price_date >= ?
        """, (latest_shares, stock_id, last_date))
        conn.commit()
        print(f"  -> Back-filled shares_outstanding={latest_shares} for {ticker_obj.ticker} up to {last_date}")

    current_beta = ticker_obj.info.get("beta", 1.0)
    cursor.execute("""
      UPDATE daily_prices
         SET beta = ?
       WHERE stock_id = ?
         AND beta IS NULL
         AND price_date <= ?
    """, (current_beta, stock_id, yesterday))
    conn.commit()
    print(f"  -> Back-filled β={current_beta:.3f} for any NULL rows up to {yesterday}")


def _get_metric(row, keys):
    """Helper to find a value in a pandas Series using a list of possible keys."""
    for key in keys:
        if key in row and pd.notna(value := row[key]):
            return value
    return None

def update_financials(conn, ticker_obj, stock_id, frequency='annual'):
    """
    Update annual or quarterly financials, ratios, and inject revenue estimates if needed.
    """
    print(f"  Updating {frequency} financials for {ticker_obj.ticker}...")
    try:
        if frequency == 'annual':
            income_stmt = ticker_obj.financials
            balance_sheet = ticker_obj.balance_sheet
            cash_flow = ticker_obj.cashflow
            
            last_year = datetime.utcnow().year - 1
            years_present = [d.year for d in income_stmt.T.index]
            
            if last_year not in years_present:
                try:
                    ttm_is = ticker_obj.ttm_financials.T
                    ttm_cf = ticker_obj.ttm_cashflow.T
                    
                    if not ttm_is.empty and not ttm_cf.empty:
                        ttm_date = pd.Timestamp(year=last_year, month=12, day=31)
                        ttm_is.index = [ttm_date]
                        ttm_cf.index = [ttm_date]
            
                    # Append and handle duplicate indices (keeping the TTM data)
                    income_stmt = pd.concat([ttm_is, income_stmt.T]).T
                    cash_flow = pd.concat([ttm_cf, cash_flow.T]).T

                    # IMPORTANT: Balance Sheet proxy (use latest BS for the TTM year)
                    if ttm_date not in balance_sheet.T.index:
                        bs_t = balance_sheet.T
                        latest_bs_row = bs_t.sort_index().iloc[[-1]].copy()
                        latest_bs_row.index = [ttm_date]
                        balance_sheet = pd.concat([latest_bs_row, bs_t]).T
                                                
                except Exception:
                    pass
            table_name = 'annual_financials'
                
        else:
            income_stmt = ticker_obj.quarterly_financials
            balance_sheet = ticker_obj.quarterly_balance_sheet
            cash_flow = ticker_obj.quarterly_cashflow
            table_name = 'quarterly_financials'
        if income_stmt.empty or balance_sheet.empty or cash_flow.empty:
            print(f"  [WARN] One or more {frequency} statements are empty for {ticker_obj.ticker}.")
            return
        income_stmt, balance_sheet, cash_flow = income_stmt.T, balance_sheet.T, cash_flow.T
        key_mapping = {
            'total_revenue': (income_stmt, ['Total Revenue']),
            'net_income': (cash_flow, ['Net Income From Continuing Operations']),
            'interest_expense': (income_stmt, ['Interest Expense']),
            'other_interest_expense': (income_stmt, ['Other Interest Expense']),
            'pretax_income': (income_stmt, ['Pretax Income']),
            'tax_provision': (income_stmt, ['Tax Provision']),
            'total_stockholder_equity': (balance_sheet, ['Stockholders Equity', 'Total Stockholder Equity']),
            'long_term_debt': (balance_sheet, ['Long Term Debt']),
            'long_term_debt_and_capital_lease_obligation': (
                balance_sheet, ['Long Term Debt And Capital Lease Obligation']),
            'current_debt': (balance_sheet, ['Current Debt']),
            'other_current_borrowings': (balance_sheet, ['Other Current Borrowings']),
            'free_cash_flow': (cash_flow, ['Free Cash Flow'])
        }
        for report_date in income_stmt.index:
            report_year = report_date.year if frequency == 'annual' else None
            # Only skip if all required annual data is already present and not for the last two years
            if frequency == 'annual' and (not (report_year and report_year >= datetime.utcnow().year - 2)):
                if not needs_update_financials(conn, stock_id, report_year, table_name):
                    print(f"    Skipping {ticker_obj.ticker} {report_year}: already present.")
                    continue
            record = {'stock_id': stock_id}
            record['report_year' if frequency == 'annual' else 'report_date'] = (
                report_year if frequency == 'annual' else report_date.strftime('%Y-%m-%d')
            )
            for db_key, (df, yf_keys) in key_mapping.items():
                if report_date in df.index:
                    value = _get_metric(df.loc[report_date], yf_keys)
                    record[db_key] = float(value) if value is not None else None
                else:
                    record[db_key] = None
            columns = ', '.join(record.keys())
            placeholders = ', '.join('?' * len(record))
            sql = f'INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})'
            conn.execute(sql, tuple(record.values()))
        # Only commit once, after all records
        conn.commit()
        print(f"  -> Processed {len(income_stmt)} {frequency} reports.")

        # Annual ratios calculation
        if frequency == 'annual':
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT report_year, free_cash_flow, net_income, total_revenue"
                " FROM annual_financials WHERE stock_id = ?",
                (stock_id,)
            ).fetchall()
            upsert_sql = '''
            INSERT INTO annual_ratios
              (stock_id, report_year, fcfe_ratio, ni_margin, revenue_growth)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(stock_id, report_year) DO UPDATE SET
              fcfe_ratio=excluded.fcfe_ratio,
              ni_margin=excluded.ni_margin,
              revenue_growth=excluded.revenue_growth;
            '''
            prev_rev = None
            for r in sorted(rows, key=lambda r: r['report_year']):
                year = r['report_year']
                fcf = r['free_cash_flow'] or 0.0
                net_income = r['net_income'] or 0.0
                rev = r['total_revenue'] or 0.0
                fcfe_ratio = (fcf / net_income) * 100 if net_income else 0.0
                ni_margin = (net_income / rev) * 100 if rev else 0.0
                revenue_growth = ((rev / prev_rev) - 1) * 100 if prev_rev and prev_rev > 0 else 0.0
                prev_rev = rev
                cursor.execute(upsert_sql, (stock_id, year, fcfe_ratio, ni_margin, revenue_growth))
            conn.commit()
            print(f"  -> Processed {len(rows)} annual ratios.")

            # Revenue estimate injection
            try:
                row = cursor.execute(
                    """
                    SELECT MAX(report_year) AS max_actual_year
                      FROM annual_financials
                     WHERE stock_id = ?
                       AND net_income IS NOT NULL
                    """,
                    (stock_id,)
                ).fetchone()
                max_actual = row['max_actual_year']
                if max_actual is not None:
                    rev_est = ticker_obj.get_revenue_estimate()
                    if not rev_est.empty and len(rev_est) >= 4:
                        est_cy = float(rev_est.iloc[2, 0] or 0.0)
                        est_ny = float(rev_est.iloc[3, 0] or 0.0)
                        for offset, value in ((1, est_cy), (2, est_ny)):
                            yr = max_actual + offset
                            existing = cursor.execute(
                                """
                                SELECT total_revenue, net_income
                                  FROM annual_financials
                                 WHERE stock_id=? AND report_year=?
                                """,
                                (stock_id, yr)
                            ).fetchone()
                            if existing is None:
                                cursor.execute(
                                    """
                                    INSERT INTO annual_financials
                                      (stock_id, report_year, total_revenue)
                                    VALUES (?, ?, ?)
                                    """,
                                    (stock_id, yr, value)
                                )
                            elif existing['net_income'] is None:
                                cursor.execute(
                                    """
                                    UPDATE annual_financials
                                       SET total_revenue = ?
                                     WHERE stock_id=? AND report_year=?
                                    """,
                                    (value, stock_id, yr)
                                )
                        conn.commit()
                        print(f"  -> Injected revenue estimates for {max_actual+1} and {max_actual+2}")
            except Exception as e:
                print(f"  [WARN] Could not inject CY/NY revenue estimates: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"  [ERROR] Could not update {frequency} financials for {ticker_obj.ticker}: {type(e).__name__}: {e}")

def process_tickers(conn, tickers):
    # This function is now simpler
    for ticker_symbol in tickers:
        print(f"\nProcessing ticker: {ticker_symbol}")
        try:
            ticker_obj = yf.Ticker(ticker_symbol)
            stock_id = update_stock_info(conn, ticker_obj)
            if stock_id:
                # First—populate annual_financials & annual_ratios so revenue_growth exists
                update_financials(conn, ticker_obj, stock_id, 'annual')
                # Now revenue_estimates injection (in update_historical_data) can see that data
                update_historical_data(conn, ticker_obj, stock_id)
                # Finally quarterly raw data
                update_financials(conn, ticker_obj, stock_id, 'quarterly')
        except Exception as e:
            print(f"[CRITICAL ERROR] Failed to process ticker {ticker_symbol}: {e}")

def process_one_ticker(ticker_symbol):
    conn = get_financial_db_conn()
    try:
        print(f"\nProcessing ticker: {ticker_symbol}")
        start = time.time()
        ticker_obj = yf.Ticker(ticker_symbol)
        stock_id = update_stock_info(conn, ticker_obj)
        if stock_id:
            update_financials(conn, ticker_obj, stock_id, 'annual')
            update_historical_data(conn, ticker_obj, stock_id)
            update_financials(conn, ticker_obj, stock_id, 'quarterly')
        print(f"  Finished {ticker_symbol} in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"[CRITICAL ERROR] Failed to process ticker {ticker_symbol}: {e}")
    finally:
        conn.close()
    time.sleep(2)

def run_data_collection(db_path):
    print(f"--- [SCHEDULER] Starting daily data collection job on db: {db_path} ---")
    db_conn = get_financial_db_conn()
    if db_conn:
        try:
            start_total = time.time()
            cursor = db_conn.cursor()
            print("\nFetching list of active tickers to track from the database...")
            rows = cursor.execute("SELECT ticker_symbol FROM tracked_tickers WHERE is_active = 1").fetchall()
            tickers_from_db = [row['ticker_symbol'] for row in rows]
            if not tickers_from_db:
                print("No active tickers found in 'tracked_tickers' table. Job finished.")
                return
            print(f"Found {len(tickers_from_db)} active tickers: {tickers_from_db}")
            
            t0 = time.time()
            try:
                update_market_data(db_conn)
                print(f"Market data update took {time.time() - t0:.2f} seconds")
            except Exception as e:
                print(f"[ERROR] Market data update failed: {e}")

            t1 = time.time()
            max_workers = min(5, len(tickers_from_db))
            print(f"Processing tickers in parallel with {max_workers} workers...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(process_one_ticker, ticker) for ticker in tickers_from_db]
                for f in as_completed(futures):
                    exc = f.exception()
                    if exc:
                        print(f"[ERROR] Exception in ticker thread: {exc}")
            print(f"Processing tickers took {time.time() - t1:.2f} seconds")
            
            t2 = time.time()
            # No need to create a new cursor, reuse the old one
            qmarks = ",".join("?" for _ in tickers_from_db)
            sql = f"SELECT id, ticker FROM stocks WHERE ticker IN ({qmarks})"
            rows = cursor.execute(sql, tickers_from_db).fetchall()
            ticker_to_id = {r["ticker"]: r["id"] for r in rows}
            stock_ids = [ticker_to_id[t] for t in tickers_from_db if t in ticker_to_id]
            for sid, symbol in zip(stock_ids, tickers_from_db):
                try:
                    persist_daily_wacc(db_conn, sid)
                    persist_daily_iv(db_conn, sid)
                except Exception as e:
                    print(f"[ERROR] daily data failed for {symbol} (ID={sid}): {type(e).__name__}: {e}")
            print(f"Daily IV/WACC processing took {time.time() - t2:.2f} seconds")
            print(f"\nTotal data collection took {time.time() - start_total:.2f} seconds")
        finally:
            db_conn.close()
            print("Database connection closed.")
    print("\n--- [SCHEDULER] Daily data collection job finished. ---")