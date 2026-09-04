from flask import Blueprint, render_template, request, flash, jsonify, current_app, session, redirect, url_for
from flask_login import login_required, current_user
from .models import Note, User, ValuationLog
import pandas as pd
from . import db
import json
from .data_jobs2 import (
    get_latest_cashflow_db, get_shares_outstanding_db,
    get_equity_growth_db, get_exchange_rate_for_ticker,
    perform_finite_horizon_dcf, persist_daily_finite_iv, get_stock_id
)
from .data_jobs import get_stock_id
from .price_utils import get_current_price
from .valuation_framework import build_company_profile, get_model_framework, get_screening_rules
from .valuation_reference import build_fcff_reference_benchmark, get_methodology_gap_notes
import os
import sqlite3
from datetime import datetime, timedelta

views = Blueprint('views', __name__, template_folder='templates', static_folder='static')


def get_financial_db_conn():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'instance', 'financial_data.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_stock_currency(conn, stock_id):
    cur = conn.cursor()
    row = cur.execute("SELECT currency FROM stocks WHERE id=?", (stock_id,)).fetchone()
    return row["currency"] if row and "currency" in row.keys() else "USD"

def get_income_statement_data(conn, stock_id, period='annual'):
    cur = conn.cursor()
    if period == 'annual':
        query = '''
        SELECT report_year, total_revenue AS revenue, net_income AS net_income, free_cash_flow
        FROM annual_financials
        WHERE stock_id=?
            AND (total_revenue IS NOT NULL AND net_income IS NOT NULL)
        ORDER BY report_year
        '''
        rows = cur.execute(query, (stock_id,)).fetchall()
        # Use report_year directly
        return [
            {
                "period": str(row["report_year"]),  # convert int to string for chart label
                "revenue": row["revenue"],
                "net_income": row["net_income"],
                "free_cash_flow": row["free_cash_flow"]
            }
            for row in rows
        ]
    else:
        query = '''
        SELECT report_date, total_revenue AS revenue, net_income AS net_income, free_cash_flow
        FROM quarterly_financials
        WHERE stock_id=?
            AND (total_revenue IS NOT NULL AND net_income IS NOT NULL)
        ORDER BY report_date
        '''
        rows = cur.execute(query, (stock_id,)).fetchall()
        def format_quarter(date_str):
            year = date_str[:4]
            month = int(date_str[5:7])
            if month in [1, 2, 3]:
                q = "Q1"
            elif month in [4, 5, 6]:
                q = "Q2"
            elif month in [7, 8, 9]:
                q = "Q3"
            else:
                q = "Q4"
            return f"{q} {year}"
        return [
            {
                "period": format_quarter(row["report_date"]),
                "revenue": row["revenue"],
                "net_income": row["net_income"],
                "free_cash_flow": row["free_cash_flow"]
            }
            for row in rows
        ]

def process_discount_rate_params(request_args_or_form, wacc_value_from_calculator=None):
    selected_rate_type = request_args_or_form.get('discount_rate_type', 'fixed_7_50')
    custom_rate_input_str = request_args_or_form.get('custom_rate_value', '')
    active_rate_type_for_calc = 'fixed_7_50'
    custom_rate_for_calc = None

    if selected_rate_type == 'wacc':
        if wacc_value_from_calculator is not None and wacc_value_from_calculator > 0:
            active_rate_type_for_calc = 'wacc'
        else:
            flash("WACC not available or invalid, using 7.50% fixed rate.", "warning")
    elif selected_rate_type == 'custom':
        try:
            custom_rate_percent = float(custom_rate_input_str)
            if 0 < custom_rate_percent < 100:
                custom_rate_for_calc = custom_rate_percent / 100.0
                active_rate_type_for_calc = 'custom'
            else:
                flash("Custom rate must be between 0 and 100. Using 7.50% fixed rate.", "error")
        except ValueError:
            flash("Invalid format for custom rate. Using 7.50% fixed rate.", "warning")

    return active_rate_type_for_calc, custom_rate_for_calc, selected_rate_type, custom_rate_input_str


def resolve_selected_discount_rate(active_rate_type, wacc_value, custom_rate):
    if active_rate_type == 'custom' and custom_rate is not None:
        return float(custom_rate)
    if active_rate_type == 'wacc' and wacc_value is not None:
        return float(wacc_value)
    return 0.075


def build_perpetual_model_context(conn, stock_id, ticker, valuation_inputs, active_rate_type, custom_rate, legacy_values):
    stock_row = conn.execute(
        "SELECT ticker, company_name, sector, industry, country, currency FROM stocks WHERE id = ?",
        (stock_id,)
    ).fetchone()
    latest_financial_row = conn.execute(
        """
        SELECT report_year, total_revenue, net_income, free_cash_flow
        FROM annual_financials
        WHERE stock_id = ?
        ORDER BY report_year DESC
        LIMIT 1
        """,
        (stock_id,)
    ).fetchone()
    latest_market_row = conn.execute(
        """
        SELECT price_date, close, shares_outstanding, total_debt
        FROM daily_prices
        WHERE stock_id = ?
        ORDER BY price_date DESC
        LIMIT 1
        """,
        (stock_id,)
    ).fetchone()

    effective_discount_rate = resolve_selected_discount_rate(active_rate_type, valuation_inputs.get("wacc"), custom_rate)
    current_price, current_price_timestamp, current_price_source = get_current_price(
        conn,
        stock_id=stock_id,
        ticker=ticker,
        prefer_realtime=True,
    )
    if current_price is None and latest_market_row:
        current_price = latest_market_row["close"]
        current_price_timestamp = latest_market_row["price_date"]
        current_price_source = "daily_prices"

    company_profile = build_company_profile(
        dict(stock_row) if stock_row else {},
        dict(latest_financial_row) if latest_financial_row else {},
    )
    framework = get_model_framework(company_profile)
    screening_rules = get_screening_rules()
    fx_rate = get_exchange_rate_for_ticker(conn, ticker)
    fcff_benchmark = build_fcff_reference_benchmark(
        latest_fcff=latest_financial_row["free_cash_flow"] if latest_financial_row else None,
        total_debt=latest_market_row["total_debt"] if latest_market_row else None,
        shares_outstanding=latest_market_row["shares_outstanding"] if latest_market_row else None,
        fx_rate=fx_rate,
        discount_rate=effective_discount_rate,
        scenario_growth_rates={
            "conservative": valuation_inputs.get("growth_lo"),
            "moderate": valuation_inputs.get("growth_md"),
            "optimistic": valuation_inputs.get("growth_hi"),
        },
        legacy_values=legacy_values,
    )

    return {
        "current_price": current_price,
        "current_price_timestamp": current_price_timestamp,
        "current_price_source": current_price_source,
        "model_framework": framework,
        "screening_rules": screening_rules,
        "methodology_gap_notes": get_methodology_gap_notes(),
        "fcff_benchmark": fcff_benchmark,
        "latest_financial_year": latest_financial_row["report_year"] if latest_financial_row else None,
        "latest_financial_fcff": latest_financial_row["free_cash_flow"] if latest_financial_row else None,
        "latest_financial_net_income": latest_financial_row["net_income"] if latest_financial_row else None,
        "latest_total_debt": latest_market_row["total_debt"] if latest_market_row else None,
    }


def get_latest_intrinsic_value(conn, stock_id, use_fixed=False):
    cur = conn.cursor()
    cols = "iv_conservative_fixed, iv_moderate_fixed, iv_optimistic_fixed" if use_fixed else \
           "iv_conservative, iv_moderate, iv_optimistic"

    row = cur.execute(f"""
        SELECT price_date, {cols}
        FROM daily_iv
        WHERE stock_id = ?
        ORDER BY price_date DESC LIMIT 1
    """, (stock_id,)).fetchone()

    if row:
        return {
            "date": row["price_date"],
            "conservative": float(row[1]) if row[1] is not None else None,
            "moderate": float(row[2]) if row[2] is not None else None,
            "optimistic": float(row[3]) if row[3] is not None else None
        }
    return None


def get_historical_iv_series(conn, stock_id, use_fixed=False):
    cur = conn.cursor()
    col_prefix = "_fixed" if use_fixed else ""
    query = f"""
        SELECT 
            price_date,
            iv_conservative{col_prefix} AS conservative,
            iv_moderate{col_prefix} AS moderate,
            iv_optimistic{col_prefix} AS optimistic
        FROM daily_iv
        WHERE stock_id = ?
        ORDER BY price_date
    """
    rows = cur.execute(query, (stock_id,)).fetchall()
    return [
        {
            "date": r["price_date"],
            "conservative": float(r[1]) if r[1] is not None else None,
            "moderate": float(r[2]) if r[2] is not None else None,
            "optimistic": float(r[3]) if r[3] is not None else None
        } for r in rows
    ]

def calculate_intrinsic_value_with_overrides_from_db(conn, stock_id, valuation_inputs):
    """
    Returns a dict of IVs for each scenario using override values when present, else DB.
    """
    # 1. Get the latest revenues, shares, wacc, and FX rate as in persist_daily_iv
    cur = conn.cursor()

    # Get the latest price_date
    price_row = cur.execute("""
        SELECT price_date, shares_outstanding, wacc_value 
        FROM daily_wacc 
        JOIN daily_prices USING(stock_id, price_date)
        WHERE stock_id = ?
        ORDER BY price_date DESC LIMIT 1
    """, (stock_id,)).fetchone()
    if not price_row:
        return None

    price_date, shares, wacc = price_row['price_date'], price_row['shares_outstanding'], price_row['wacc_value']

    # Get revenues for latest two years
    def get_revs(Y):
        row1 = cur.execute("SELECT total_revenue FROM annual_financials WHERE stock_id=? AND report_year=?",
                           (stock_id, Y)).fetchone()
        r1 = float(row1['total_revenue']) if row1 and row1['total_revenue'] is not None else 0.0

        row2 = cur.execute("SELECT total_revenue FROM annual_financials WHERE stock_id=? AND report_year=?",
                           (stock_id, Y+1)).fetchone()
        r2 = float(row2['total_revenue']) if row2 and row2['total_revenue'] is not None else 0.0

        return r1, r2

    Y = int(price_date[:4])
    rev1, rev2 = get_revs(Y)

    # 2. Get override or DB values for each scenario
    # If an override exists in valuation_inputs, use it; else pull from DB as in data_jobs.py

    def get_scenarios(kind, default_func):
        return (
            float(valuation_inputs.get(f'{kind}_lo', None)) if valuation_inputs.get(f'{kind}_lo', None) is not None else default_func('lo'),
            float(valuation_inputs.get(f'{kind}_md', None)) if valuation_inputs.get(f'{kind}_md', None) is not None else default_func('md'),
            float(valuation_inputs.get(f'{kind}_hi', None)) if valuation_inputs.get(f'{kind}_hi', None) is not None else default_func('hi'),
        )

    def get_growth_default(level):
        return cur.execute(f"SELECT growth_{level} FROM latest_parameters WHERE stock_id=? AND price_date=?",
                           (stock_id, price_date)).fetchone()[0] or 0.0

    def get_fcfe_default(level):
        return cur.execute(f"SELECT fcfe_{level} FROM latest_parameters WHERE stock_id=? AND price_date=?",
                           (stock_id, price_date)).fetchone()[0] or 0.0

    def get_margin_default(level):
        return cur.execute(f"SELECT margin_{level} FROM latest_parameters WHERE stock_id=? AND price_date=?",
                           (stock_id, price_date)).fetchone()[0] or 0.0

    g_lo, g_md, g_hi = get_scenarios('growth', get_growth_default)
    f_lo, f_md, f_hi = get_scenarios('fcfe', get_fcfe_default)
    m_lo, m_md, m_hi = get_scenarios('margin', get_margin_default)

    # 3. dcf_fcfe calculation (from data_jobs.py)
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

    # 4. Get FX rate as in get_exchange_rate_for_ticker
    rate = get_exchange_rate_for_ticker(conn, cur.execute("SELECT ticker FROM stocks WHERE id=?", (stock_id,)).fetchone()["ticker"])

    # 5. Discount rate logic (for now, use wacc as default, or allow custom/fixed)
    discount_type = valuation_inputs.get('discount_type', 'fixed_7_50')
    custom_rate = valuation_inputs.get('custom_rate', None)
    if discount_type == 'custom' and custom_rate is not None:
        disc = float(custom_rate)
    elif discount_type == 'wacc' and wacc:
        disc = float(wacc)
    else:
        disc = 0.075

    # 6. Calculate IV per scenario
    scenarios = {
        'conservative': (g_lo, m_lo, f_lo),
        'moderate': (g_md, m_md, f_md),
        'optimistic': (g_hi, m_hi, f_hi),
    }
    ivs = {}
    for name, (g, m, f) in scenarios.items():
        iv = dcf_fcfe(rev1, rev2, g, m, f, disc)
        ivs[name] = (iv / (shares * rate)) if shares and shares > 0 else 0.0

    return ivs

def get_historical_iv_series_with_overrides(conn, stock_id, valuation_inputs):
    """
    Returns the historical IV series using override values for scenarios where present.
    For non-overridden scenarios, uses the DB value.
    """
    # 1. Fetch all historical price_dates (with shares, wacc, etc.)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT p.price_date, p.shares_outstanding, w.wacc_value
        FROM daily_prices p
        JOIN daily_wacc w ON p.stock_id = w.stock_id AND p.price_date = w.price_date
        WHERE p.stock_id = ?
        ORDER BY p.price_date
    """, (stock_id,)).fetchall()

    if not rows:
        return []

    # 2. Get the ticker and FX rate (cached)
    ticker_row = cur.execute("SELECT ticker FROM stocks WHERE id=?", (stock_id,)).fetchone()
    if not ticker_row:
        return []
    from .data_jobs import get_exchange_rate_for_ticker
    rate = get_exchange_rate_for_ticker(conn, ticker_row["ticker"])

    # 3. Pre-fetch all annual financials into a dict for fast lookup
    annual_financials = {}
    af_rows = cur.execute("SELECT report_year, total_revenue FROM annual_financials WHERE stock_id=?", (stock_id,)).fetchall()
    for r in af_rows:
        annual_financials[r['report_year']] = r['total_revenue']

    # 4. Pre-fetch all scenario parameters for all dates from latest_parameters
    param_rows = cur.execute("SELECT price_date, growth_lo, growth_md, growth_hi, fcfe_lo, fcfe_md, fcfe_hi, margin_lo, margin_md, margin_hi FROM latest_parameters WHERE stock_id=?", (stock_id,)).fetchall()
    params_by_date = {row["price_date"]: row for row in param_rows}

    # 5. Prepare override logic
    def get_override(kind, scenario):
        # Map scenario to key suffix
        suffix_map = {
            "conservative": "lo",
            "moderate": "md",
            "optimistic": "hi",
        }
        override_key = f"{kind}_{suffix_map[scenario]}"
        return float(valuation_inputs.get(override_key)) if valuation_inputs.get(override_key) is not None else None

    # 6. Discount rate logic
    discount_type = valuation_inputs.get('discount_type', 'fixed_7_50')
    custom_rate = valuation_inputs.get('custom_rate', None)

    # 7. For each row/date, recalculate IV using overrides for overridden scenarios; else use DB value
    series = []
    for row in rows:
        price_date, shares, wacc = row['price_date'], row['shares_outstanding'], row['wacc_value']
        Y = int(price_date[:4])
        rev1 = float(annual_financials.get(Y, 0.0) or 0.0)
        rev2 = float(annual_financials.get(Y+1, 0.0) or 0.0)
        param_row = params_by_date.get(price_date)
        scenario_ivs = {}

        for scenario in ["conservative", "moderate", "optimistic"]:
            # If user override present for this scenario, use it; else from DB
            g = get_override("growth", scenario)
            m = get_override("margin", scenario)
            f = get_override("fcfe", scenario)
            # If no override, fallback to DB values for that date
            suffix_map = {"conservative": "lo", "moderate": "md", "optimistic": "hi"}
            suf = suffix_map[scenario]
            if g is None and param_row: g = param_row[f"growth_{suf}"]
            if m is None and param_row: m = param_row[f"margin_{suf}"]
            if f is None and param_row: f = param_row[f"fcfe_{suf}"]

            # Discount rate
            if discount_type == 'custom' and custom_rate is not None:
                disc = float(custom_rate)
            elif discount_type == 'wacc' and wacc:
                disc = float(wacc)
            else:
                disc = 0.075

            # dcf_fcfe calculation as before
            def dcf_fcfe(r1, r2, g_pct, m_pct, f_pct, disc):
                g_ = g_pct / 100.0
                m_ = m_pct / 100.0
                f_ = f_pct / 100.0
                fc1 = r1 * m_ * f_
                fc2 = r2 * m_ * f_
                fc3 = fc2 * (1 + g_)
                fc4 = fc3 * (1 + g_)
                df = [(1+disc)**i for i in (1,2,3,4)]
                pv_front = fc1/df[0] + fc2/df[1] + fc3/df[2]
                perp = 0.025
                tv = (fc4*(1+perp))/(disc-perp) if disc>perp else fc4
                pv_term = (fc4 + tv)/df[3]
                return pv_front + pv_term

            if shares and shares > 0:
                iv = dcf_fcfe(rev1, rev2, g or 0.0, m or 0.0, f or 0.0, disc)
                scenario_ivs[scenario] = iv / (shares * rate)
            else:
                scenario_ivs[scenario] = 0.0

        # Add the result for this date
        series.append({
            "date": price_date,
            "conservative": scenario_ivs["conservative"],
            "moderate": scenario_ivs["moderate"],
            "optimistic": scenario_ivs["optimistic"],
        })

    return series

# --- ALL ROUTES BELOW (unchanged, except for historical IV calculation in value_stock_lookup_from_db) ---

@views.route('/', methods=['GET', 'POST'])
@login_required
def home():
    if request.method == 'POST':
        note_data = request.form.get('note')
        if not note_data or len(note_data) < 1:
            flash('Note is too short!', category='error')    
        else:
            new_note = Note(data=note_data, user_id=current_user.id) 
            db.session.add(new_note) 
            db.session.commit()
            flash('Note added!', category='success')
            
    return render_template("home.html", user=current_user)

@views.route('/delete-note', methods=['POST'])
@login_required
def delete_note():
    try:
        note_payload = json.loads(request.data)
        noteId = note_payload.get('noteId')
        if noteId is None:
            return jsonify({"error": "noteId missing"}), 400
            
        note = Note.query.get(noteId)
        if note:
            if note.user_id == current_user.id:
                db.session.delete(note)
                db.session.commit()
                return jsonify({"success": True})
            else:
                return jsonify({"error": "Permission denied"}), 403
        else:
            return jsonify({"error": "Note not found"}), 404
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON payload"}), 400
    except Exception as e:
        current_app.logger.error(f"Error deleting note: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

@views.route('/value-stock', methods=['GET'])
@login_required
def value_stock_get_ticker_page():
    session.pop('valuation_ticker', None)
    session.pop('valuation_defaults', None)
    session.pop('valuation_raw_displayName', None)
    session.pop('valuation_applied_overrides', None)
    return render_template("value_stock_form.html", user=current_user)

@views.route('/value-stock/overrides', methods=['GET', 'POST'])
@login_required
def value_stock_get_overrides_page():
    ticker = session.get('valuation_ticker')
    defaults = session.get('valuation_defaults')
    raw_displayName = session.get('valuation_raw_displayName', ticker.upper() if ticker else 'Selected Stock')

    if not ticker or not defaults:
        flash("Session expired. Please start again.", "warning")
        return redirect(url_for("views.value_stock_get_ticker_page"))

    return render_template("value_stock_overrides_form.html",
                           user=current_user,
                           ticker=ticker,
                           raw_displayName=raw_displayName,
                           defaults=defaults,
                           scenario_names=["Conservative", "Moderate", "Optimistic"])

@views.route('/value-stock/lookup', methods=['POST', 'GET'])
@login_required
def value_stock_lookup_from_db():
    try:
        # Step 1: Ticker management
        if request.method == 'GET':
            ticker = session.get("valuation_ticker")
            if not ticker:
                flash("No stock selected. Please start a valuation.", "warning")
                return redirect(url_for("views.value_stock_get_ticker_page"))
        else:
            ticker = request.form.get("ticker")
            if ticker:
                session["valuation_ticker"] = ticker

        if not ticker:
            flash("Missing ticker.", "error")
            return redirect(url_for("views.value_stock_get_ticker_page"))

        conn = get_financial_db_conn()
        stock_id = get_stock_id(conn, ticker)

        currency = get_stock_currency(conn, stock_id)
        annual_income_data = get_income_statement_data(conn, stock_id, 'annual')
        quarterly_income_data = get_income_statement_data(conn, stock_id, 'quarterly')
        
        # Step 2: DB defaults
        valuation_inputs_row = conn.cursor().execute("""
            SELECT
                lp.growth_lo, lp.growth_md, lp.growth_hi,
                lp.fcfe_lo, lp.fcfe_md, lp.fcfe_hi,
                lp.margin_lo, lp.margin_md, lp.margin_hi,
                dw.wacc_value
            FROM latest_parameters lp
            JOIN daily_wacc dw ON lp.stock_id = dw.stock_id AND lp.price_date = dw.price_date
            WHERE lp.stock_id = ?
            ORDER BY lp.price_date DESC
            LIMIT 1
        """, (stock_id,)).fetchone()

        wacc = valuation_inputs_row[9] if valuation_inputs_row else None
        valuation_inputs = {
            "growth_lo": valuation_inputs_row[0], "growth_md": valuation_inputs_row[1], "growth_hi": valuation_inputs_row[2],
            "fcfe_lo": valuation_inputs_row[3], "fcfe_md": valuation_inputs_row[4], "fcfe_hi": valuation_inputs_row[5],
            "margin_lo": valuation_inputs_row[6], "margin_md": valuation_inputs_row[7], "margin_hi": valuation_inputs_row[8],
            "wacc": wacc
        } if valuation_inputs_row else {}

        # Step 3: Handle overrides
        if request.method == 'POST' and "rg_bear" in request.form:
            overrides = {
                'rg_bear': float(request.form.get('rg_bear')),
                'rg_base': float(request.form.get('rg_base')),
                'rg_bull': float(request.form.get('rg_bull')),
                'fcfe_bear': float(request.form.get('fcfe_bear')),
                'fcfe_base': float(request.form.get('fcfe_base')),
                'fcfe_bull': float(request.form.get('fcfe_bull')),
                'nim_bear': float(request.form.get('nim_bear')),
                'nim_base': float(request.form.get('nim_base')),
                'nim_bull': float(request.form.get('nim_bull')),
            }
            session['valuation_applied_overrides'] = overrides
        overrides = session.get("valuation_applied_overrides")

        if overrides:
            valuation_inputs.update({
                "growth_lo": overrides.get("rg_bear", valuation_inputs["growth_lo"]),
                "growth_md": overrides.get("rg_base", valuation_inputs["growth_md"]),
                "growth_hi": overrides.get("rg_bull", valuation_inputs["growth_hi"]),
                "fcfe_lo": overrides.get("fcfe_bear", valuation_inputs["fcfe_lo"]),
                "fcfe_md": overrides.get("fcfe_base", valuation_inputs["fcfe_md"]),
                "fcfe_hi": overrides.get("fcfe_bull", valuation_inputs["fcfe_hi"]),
                "margin_lo": overrides.get("nim_bear", valuation_inputs["margin_lo"]),
                "margin_md": overrides.get("nim_base", valuation_inputs["margin_md"]),
                "margin_hi": overrides.get("nim_bull", valuation_inputs["margin_hi"]),
            })
        
        # --- Standardized Negative Metric Warning for WACC ---
        wacc_negative_metrics = []
        for k, label in [
            ("fcfe_lo", "FCFE Ratio (Conservative)"),
            ("fcfe_md", "FCFE Ratio (Moderate)"),
            ("fcfe_hi", "FCFE Ratio (Optimistic)")
        ]:
            val = valuation_inputs.get(k)
            if val is not None and val < 0:
                wacc_negative_metrics.append(f"{label}: {val:.2f}%")
        for k, label in [
            ("margin_lo", "Net Income Margin (Conservative)"),
            ("margin_md", "Net Income Margin (Moderate)"),
            ("margin_hi", "Net Income Margin (Optimistic)")
        ]:
            val = valuation_inputs.get(k)
            if val is not None and val < 0:
                wacc_negative_metrics.append(f"{label}: {val:.2f}%")
        if wacc_negative_metrics:
            flash(
                "⚠️ Warning: Negative metric(s) detected in WACC calculation that may result in negative IV value. Please review the following: "
                + ", ".join(wacc_negative_metrics),
                "warning"
            )
        
        # Step 4: Discount rate handling
        active_rate_type, custom_rate, selected_rate_type, custom_rate_input_str = process_discount_rate_params(request.form, wacc)
        use_fixed = (active_rate_type == "fixed_7_50")
        selected_scenario = request.form.get("comparison_scenario", "moderate")

        # Calculate IVs at both WACC and 7.5% fixed for all scenarios
        def get_iv_for_rate(rate):
            inputs = dict(valuation_inputs)
            inputs['discount_type'] = 'custom'
            inputs['custom_rate'] = rate
            return calculate_intrinsic_value_with_overrides_from_db(conn, stock_id, inputs)

        # IVs at WACC (actual)
        iv_wacc = get_iv_for_rate(wacc) if wacc else {'conservative': None, 'moderate': None, 'optimistic': None}
        # IVs at 7.5% fixed
        iv_fixed = get_iv_for_rate(0.075)

        # For your normal display logic
        if overrides:
            merged_inputs = {**valuation_inputs, **overrides}
            merged_inputs['discount_type'] = active_rate_type
            merged_inputs['custom_rate'] = custom_rate
            iv_values_only = calculate_intrinsic_value_with_overrides_from_db(conn, stock_id, merged_inputs)
            iv_series = get_historical_iv_series_with_overrides(conn, stock_id, merged_inputs)
            iv = {
                "conservative": iv_values_only.get("conservative"),
                "moderate": iv_values_only.get("moderate"),
                "optimistic": iv_values_only.get("optimistic"),
                "date":  None
            }
        else:
            iv = get_latest_intrinsic_value(conn, stock_id, use_fixed=use_fixed)
            iv_series = get_historical_iv_series(conn, stock_id, use_fixed=use_fixed)
            iv_values_only = {k: iv[k] for k in ["conservative", "moderate", "optimistic"]} if iv else {}

        comparison_context = build_perpetual_model_context(
            conn=conn,
            stock_id=stock_id,
            ticker=ticker,
            valuation_inputs=valuation_inputs,
            active_rate_type=active_rate_type,
            custom_rate=custom_rate,
            legacy_values=iv_values_only,
        )

        price_row = conn.execute(
            "SELECT close, price_date FROM daily_prices WHERE stock_id = ? ORDER BY price_date DESC LIMIT 1",
            (stock_id,)
        ).fetchone()

        candlestick_data = [
            {
                "x": row["price_date"],
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"])
            } for row in conn.execute(
                "SELECT price_date, open, high, low, close FROM daily_prices WHERE stock_id = ? ORDER BY price_date",
                (stock_id,)
            ).fetchall() if None not in (row["open"], row["high"], row["low"], row["close"])
        ]

        raw_displayName = conn.execute("SELECT company_name FROM stocks WHERE id = ?", (stock_id,)).fetchone()
        conn.close()

        display_name = raw_displayName["company_name"] if raw_displayName else ticker.upper()
        assumptions_are_modified = bool(overrides)

        session["valuation_defaults"] = {
            'rev_growth_html': '', 'fcfe_html': '', 'ni_margin_html': '',
            'rg_bear': valuation_inputs["growth_lo"],
            'rg_base': valuation_inputs["growth_md"],
            'rg_bull': valuation_inputs["growth_hi"],
            'fcfe_bear': valuation_inputs["fcfe_lo"],
            'fcfe_base': valuation_inputs["fcfe_md"],
            'fcfe_bull': valuation_inputs["fcfe_hi"],
            'nim_bear': valuation_inputs["margin_lo"],
            'nim_base': valuation_inputs["margin_md"],
            'nim_bull': valuation_inputs["margin_hi"],
        }

        # ---- Valuation Summary Update ----
        summary = session.get('valuation_summary', [])
        
        selected_scenario = request.form.get("comparison_scenario") or "moderate"
        iv_wacc_selected = iv_wacc.get(selected_scenario) if iv_wacc else None
        price_val = comparison_context["current_price"]
        pct = None
        status = None
        if iv_wacc_selected is not None and price_val:
            pct = ((iv_wacc_selected - price_val) / price_val) * 100
            status = "Undervalued" if pct > 0 else "Overvalued"
        req_return = f"{(wacc*100):.2f}%" if wacc is not None else ""
        update_fields = {
            "model_type": "wacc",
            "iv_wacc_conservative": iv_wacc.get("conservative") if iv_wacc else None,
            "iv_wacc_moderate": iv_wacc.get("moderate") if iv_wacc else None,
            "iv_wacc_optimistic": iv_wacc.get("optimistic") if iv_wacc else None,
            "iv_fixed_conservative": iv_fixed.get("conservative") if iv_fixed else None,
            "iv_fixed_moderate": iv_fixed.get("moderate") if iv_fixed else None,
            "iv_fixed_optimistic": iv_fixed.get("optimistic") if iv_fixed else None,
            "wacc_value": wacc,
            "required_return": req_return,
            "price": price_val,
            "pct": pct,
            "status": status,
            "updated": datetime.utcnow().strftime("%d/%m/%Y"),
        }
        summary = upsert_summary(summary, ticker, update_fields)
        session['valuation_summary'] = summary
        session.modified = True
        # ---- End Valuation Summary Update ----

        return render_template("value_stock_result_db.html",
            user=current_user,
            ticker=ticker,
            raw_displayName=display_name,
            currency=currency,
            annual_income_data=annual_income_data,
            quarterly_income_data=quarterly_income_data,
            latest_iv=iv,
            iv_series_json=json.dumps(iv_series, default=str),
            iv_values_json=json.dumps(iv_values_only),
            candlestick_json=json.dumps(candlestick_data, default=str),
            current_price=comparison_context["current_price"],
            latest_date=price_row["price_date"] if price_row else None,
            current_price_timestamp=comparison_context["current_price_timestamp"],
            current_price_source=comparison_context["current_price_source"],
            using_fixed=use_fixed,
            selected_rate_type=selected_rate_type,
            selected_discount_option=selected_rate_type,
            selected_custom_rate=custom_rate_input_str,
            selected_comparison_scenario=selected_scenario,
            calculated_wacc_rate=wacc,
            valuation_inputs=valuation_inputs,
            scenarios_list=["conservative", "moderate", "optimistic"],
            assumptions_are_modified=assumptions_are_modified,
            model_framework=comparison_context["model_framework"],
            screening_rules=comparison_context["screening_rules"],
            methodology_gap_notes=comparison_context["methodology_gap_notes"],
            fcff_benchmark=comparison_context["fcff_benchmark"],
            latest_financial_year=comparison_context["latest_financial_year"],
            latest_financial_fcff=comparison_context["latest_financial_fcff"],
            latest_financial_net_income=comparison_context["latest_financial_net_income"],
            latest_total_debt=comparison_context["latest_total_debt"],
        )

    except Exception as e:
        current_app.logger.error(f"Error in value_stock_lookup_from_db: {e}", exc_info=True)
        flash("Invalid ticker symbol. Please check your entry and try again.", "error")
        return redirect(url_for("views.value_stock_get_ticker_page"))

@views.route('/value-stock/update-results-db', methods=['POST'])
@login_required
def update_valuation_results_db():
    ticker = request.form.get('ticker_symbol_for_recalc')
    if not ticker:
        flash("Ticker symbol missing for recalculation.", "error")
        return redirect(url_for('views.value_stock_get_ticker_page'))

    try:
        conn = get_financial_db_conn()
        stock_id = get_stock_id(conn, ticker)
        
        currency = get_stock_currency(conn, stock_id)
        annual_income_data = get_income_statement_data(conn, stock_id, 'annual')
        quarterly_income_data = get_income_statement_data(conn, stock_id, 'quarterly')
        
        if not stock_id:
            flash("Stock not found in database.", "error")
            return redirect(url_for('views.value_stock_get_ticker_page'))

        valuation_inputs = conn.cursor().execute("""
            SELECT
                lp.growth_lo, lp.growth_md, lp.growth_hi,
                lp.fcfe_lo, lp.fcfe_md, lp.fcfe_hi,
                lp.margin_lo, lp.margin_md, lp.margin_hi,
                dw.wacc_value
            FROM latest_parameters lp
            JOIN daily_wacc dw ON lp.stock_id = dw.stock_id AND lp.price_date = dw.price_date
            WHERE lp.stock_id = ?
            ORDER BY lp.price_date DESC
            LIMIT 1
        """, (stock_id,)).fetchone()

        wacc = valuation_inputs[9] if valuation_inputs else None
        valuation_inputs_dict = {
            "growth_lo": valuation_inputs[0],
            "growth_md": valuation_inputs[1],
            "growth_hi": valuation_inputs[2],
            "fcfe_lo": valuation_inputs[3],
            "fcfe_md": valuation_inputs[4],
            "fcfe_hi": valuation_inputs[5],
            "margin_lo": valuation_inputs[6],
            "margin_md": valuation_inputs[7],
            "margin_hi": valuation_inputs[8],
            "wacc": wacc
        } if valuation_inputs else {}

        # --- discount rate logic ---
        active_rate_type_for_calc, custom_rate, selected_rate_type, custom_rate_input_str = process_discount_rate_params(request.form, wacc)
        use_fixed = (active_rate_type_for_calc == 'fixed_7_50')
        selected_scenario = request.form.get("comparison_scenario") or "moderate"

        # ONLY update session on a real assumption POST (not just discount rate switch)
        # This is not needed here, as update-results-db is not the override form.
        # So just use whatever is in session:
        overrides = session.get("valuation_applied_overrides")
        assumptions_are_modified = bool(overrides)

        if overrides:
            merged_inputs = {**valuation_inputs_dict, **overrides}
            merged_inputs["growth_lo"] = overrides.get("rg_bear", merged_inputs["growth_lo"])
            merged_inputs["growth_md"] = overrides.get("rg_base", merged_inputs["growth_md"])
            merged_inputs["growth_hi"] = overrides.get("rg_bull", merged_inputs["growth_hi"])
            merged_inputs["fcfe_lo"] = overrides.get("fcfe_bear", merged_inputs["fcfe_lo"])
            merged_inputs["fcfe_md"] = overrides.get("fcfe_base", merged_inputs["fcfe_md"])
            merged_inputs["fcfe_hi"] = overrides.get("fcfe_bull", merged_inputs["fcfe_hi"])
            merged_inputs["margin_lo"] = overrides.get("nim_bear", merged_inputs["margin_lo"])
            merged_inputs["margin_md"] = overrides.get("nim_base", merged_inputs["margin_md"])
            merged_inputs["margin_hi"] = overrides.get("nim_bull", merged_inputs["margin_hi"])
            merged_inputs['discount_type'] = active_rate_type_for_calc
            merged_inputs['custom_rate'] = custom_rate
        
            iv_values_only = calculate_intrinsic_value_with_overrides_from_db(conn, stock_id, merged_inputs)
            iv_series = get_historical_iv_series_with_overrides(conn, stock_id, merged_inputs)
            iv = {
                "conservative": iv_values_only.get("conservative"),
                "moderate": iv_values_only.get("moderate"),
                "optimistic": iv_values_only.get("optimistic"),
                "date": None
            }
        else:
            merged_inputs = dict(valuation_inputs_dict)  # make a copy to avoid mutating original
            merged_inputs['discount_type'] = active_rate_type_for_calc
            merged_inputs['custom_rate'] = custom_rate
            # Use your custom calculation function ONLY if not using fixed (so custom and wacc handled)
            if active_rate_type_for_calc in ('custom', 'wacc'):
                iv = calculate_intrinsic_value_with_overrides_from_db(conn, stock_id, merged_inputs)
                iv_series = get_historical_iv_series_with_overrides(conn, stock_id, merged_inputs)
                iv_values_only = {k: iv[k] for k in ["conservative", "moderate", "optimistic"]} if iv else {}
            else:
                iv = get_latest_intrinsic_value(conn, stock_id, use_fixed=True)
                iv_series = get_historical_iv_series(conn, stock_id, use_fixed=True)
                iv_values_only = {k: iv[k] for k in ["conservative", "moderate", "optimistic"]} if iv else {}

        comparison_context = build_perpetual_model_context(
            conn=conn,
            stock_id=stock_id,
            ticker=ticker,
            valuation_inputs=merged_inputs,
            active_rate_type=active_rate_type_for_calc,
            custom_rate=custom_rate,
            legacy_values=iv_values_only,
        )

        price_rows = conn.cursor().execute("""
            SELECT price_date, open, high, low, close
            FROM daily_prices
            WHERE stock_id = ?
            ORDER BY price_date
        """, (stock_id,)).fetchall()

        candlestick_data = [
            {
                "x": row["price_date"],
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"])
            }
            for row in price_rows
            if None not in (row["open"], row["high"], row["low"], row["close"])
        ]

        price_row = conn.cursor().execute(
            "SELECT close, price_date FROM daily_prices WHERE stock_id=? ORDER BY price_date DESC LIMIT 1",
            (stock_id,)
        ).fetchone()

        current_price = comparison_context["current_price"]
        latest_date = price_row["price_date"] if price_row else None
        
        display_name_row = conn.execute("SELECT company_name FROM stocks WHERE id = ?", (stock_id,)).fetchone()
        display_name = display_name_row["company_name"] if display_name_row else ticker.upper()
        conn.close()
        
        return render_template("value_stock_result_db.html",
                               user=current_user,
                               ticker=ticker,
                               raw_displayName=display_name,
                               currency=currency,
                               annual_income_data=annual_income_data,
                               quarterly_income_data=quarterly_income_data,
                               latest_iv=iv,
                               iv_series_json=json.dumps(iv_series, default=str),
                               iv_values_json=json.dumps(iv_values_only),
                               candlestick_json=json.dumps(candlestick_data, default=str),
                               current_price=current_price,
                               latest_date=latest_date,
                               current_price_timestamp=comparison_context["current_price_timestamp"],
                               current_price_source=comparison_context["current_price_source"],
                               using_fixed=use_fixed,
                               selected_rate_type=selected_rate_type,
                               selected_comparison_scenario=selected_scenario,
                               custom_rate_input_str=custom_rate_input_str,
                               valuation_inputs=merged_inputs,
                               scenarios_list=["conservative", "moderate", "optimistic"],
                               calculated_wacc_rate=wacc,
                               selected_discount_option=selected_rate_type,
                               selected_custom_rate=custom_rate_input_str,
                               assumptions_are_modified=assumptions_are_modified,
                               active_page="wacc",
                               model_framework=comparison_context["model_framework"],
                               screening_rules=comparison_context["screening_rules"],
                               methodology_gap_notes=comparison_context["methodology_gap_notes"],
                               fcff_benchmark=comparison_context["fcff_benchmark"],
                               latest_financial_year=comparison_context["latest_financial_year"],
                               latest_financial_fcff=comparison_context["latest_financial_fcff"],
                               latest_financial_net_income=comparison_context["latest_financial_net_income"],
                               latest_total_debt=comparison_context["latest_total_debt"])
    except Exception as e:
        current_app.logger.error(f"[ERROR] update_valuation_results_db failed: {e}", exc_info=True)
        flash("Something went wrong during the update.", "error")
        return redirect(url_for('views.value_stock_get_ticker_page'))


@views.route("/value-stock/reset-defaults-db", methods=["POST"])
@login_required
def reset_valuation_defaults_db():
    try:
        ticker = request.form.get("ticker_symbol_for_reset")
        if not ticker:
            flash("Missing ticker for reset.", "danger")
            return redirect(url_for("views.value_stock_get_ticker_page"))

        # Step 1: Clear session overrides
        session.pop("valuation_applied_overrides", None)
        session.pop("valuation_defaults", None)

        conn = get_financial_db_conn()
        stock_id = get_stock_id(conn, ticker)
        
        currency = get_stock_currency(conn, stock_id)
        annual_income_data = get_income_statement_data(conn, stock_id, 'annual')
        quarterly_income_data = get_income_statement_data(conn, stock_id, 'quarterly')
        
        valuation_inputs_row = conn.cursor().execute("""
            SELECT
                lp.growth_lo, lp.growth_md, lp.growth_hi,
                lp.fcfe_lo, lp.fcfe_md, lp.fcfe_hi,
                lp.margin_lo, lp.margin_md, lp.margin_hi,
                dw.wacc_value
            FROM latest_parameters lp
            JOIN daily_wacc dw ON lp.stock_id = dw.stock_id AND lp.price_date = dw.price_date
            WHERE lp.stock_id = ?
            ORDER BY lp.price_date DESC
            LIMIT 1
        """, (stock_id,)).fetchone()

        if not valuation_inputs_row:
            flash("Unable to load valuation inputs.", "danger")
            return redirect(url_for("views.value_stock_get_ticker_page"))

        wacc = valuation_inputs_row[9]
        valuation_inputs = {
            "growth_lo": valuation_inputs_row[0], "growth_md": valuation_inputs_row[1], "growth_hi": valuation_inputs_row[2],
            "fcfe_lo": valuation_inputs_row[3], "fcfe_md": valuation_inputs_row[4], "fcfe_hi": valuation_inputs_row[5],
            "margin_lo": valuation_inputs_row[6], "margin_md": valuation_inputs_row[7], "margin_hi": valuation_inputs_row[8],
            "wacc": wacc
        }

        # Store back into session for override page
        session["valuation_defaults"] = {
            'rev_growth_html': '', 'fcfe_html': '', 'ni_margin_html': '',
            'rg_bear': valuation_inputs["growth_lo"],
            'rg_base': valuation_inputs["growth_md"],
            'rg_bull': valuation_inputs["growth_hi"],
            'fcfe_bear': valuation_inputs["fcfe_lo"],
            'fcfe_base': valuation_inputs["fcfe_md"],
            'fcfe_bull': valuation_inputs["fcfe_hi"],
            'nim_bear': valuation_inputs["margin_lo"],
            'nim_base': valuation_inputs["margin_md"],
            'nim_bull': valuation_inputs["margin_hi"],
        }

        # Discount logic
        active_rate_type = "fixed_7_50"
        use_fixed = True
        custom_rate_input_str = ""
        selected_scenario = "moderate"

        iv = get_latest_intrinsic_value(conn, stock_id, use_fixed=use_fixed)
        iv_series = get_historical_iv_series(conn, stock_id, use_fixed=use_fixed)
        iv_values_only = {k: iv[k] for k in ["conservative", "moderate", "optimistic"]} if iv else {}

        comparison_context = build_perpetual_model_context(
            conn=conn,
            stock_id=stock_id,
            ticker=ticker,
            valuation_inputs=valuation_inputs,
            active_rate_type=active_rate_type,
            custom_rate=None,
            legacy_values=iv_values_only,
        )

        price_row = conn.execute(
            "SELECT close, price_date FROM daily_prices WHERE stock_id = ? ORDER BY price_date DESC LIMIT 1",
            (stock_id,)
        ).fetchone()

        candlestick_data = [
            {
                "x": row["price_date"],
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"])
            } for row in conn.execute(
                "SELECT price_date, open, high, low, close FROM daily_prices WHERE stock_id = ? ORDER BY price_date",
                (stock_id,)
            ).fetchall() if None not in (row["open"], row["high"], row["low"], row["close"])
        ]

        display_name_row = conn.execute("SELECT company_name FROM stocks WHERE id = ?", (stock_id,)).fetchone()
        display_name = display_name_row["company_name"] if display_name_row else ticker.upper()
        conn.close()

        return render_template("value_stock_result_db.html",
            user=current_user,
            ticker=ticker,
            raw_displayName=display_name,
            currency=currency,
            annual_income_data=annual_income_data,
            quarterly_income_data=quarterly_income_data,
            latest_iv=iv,
            iv_series_json=json.dumps(iv_series, default=str),
            iv_values_json=json.dumps(iv_values_only),
            candlestick_json=json.dumps(candlestick_data, default=str),
            current_price=comparison_context["current_price"],
            latest_date=price_row["price_date"] if price_row else None,
            current_price_timestamp=comparison_context["current_price_timestamp"],
            current_price_source=comparison_context["current_price_source"],
            using_fixed=True,
            selected_rate_type=active_rate_type,
            selected_discount_option=active_rate_type,
            selected_custom_rate=custom_rate_input_str,
            selected_comparison_scenario=selected_scenario,
            calculated_wacc_rate=wacc,
            valuation_inputs=valuation_inputs,
            scenarios_list=["conservative", "moderate", "optimistic"],
            assumptions_are_modified=False,
            model_framework=comparison_context["model_framework"],
            screening_rules=comparison_context["screening_rules"],
            methodology_gap_notes=comparison_context["methodology_gap_notes"],
            fcff_benchmark=comparison_context["fcff_benchmark"],
            latest_financial_year=comparison_context["latest_financial_year"],
            latest_financial_fcff=comparison_context["latest_financial_fcff"],
            latest_financial_net_income=comparison_context["latest_financial_net_income"],
            latest_total_debt=comparison_context["latest_total_debt"],
        )

    except Exception as e:
        flash(f"Reset failed: {e}", "danger")
        current_app.logger.error(f"Error during reset: {e}", exc_info=True)
        return redirect(url_for("views.value_stock_get_ticker_page"))

def get_latest_finite_horizon_iv(conn, stock_id):
    cur = conn.cursor()
    row = cur.execute("""
        SELECT price_date, iv_conservative, iv_moderate, iv_optimistic
        FROM daily_finite_iv
        WHERE stock_id = ?
        ORDER BY price_date DESC LIMIT 1
    """, (stock_id,)).fetchone()
    if row:
        return {
            "date": row["price_date"],
            "conservative": float(row["iv_conservative"]) if row["iv_conservative"] is not None else None,
            "moderate": float(row["iv_moderate"]) if row["iv_moderate"] is not None else None,
            "optimistic": float(row["iv_optimistic"]) if row["iv_optimistic"] is not None else None
        }
    return None

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

def get_finite_horizon_iv_and_persist(conn, ticker, projection_years, discount_rate, margin_of_safety, growth_rates=None, cash_flow=None):
    """
    Calculate finite horizon IVs for all scenarios, persist to daily_finite_iv, and return results.
    Uses analyst_growth_estimates SQL table instead of yfinance scraping.
    cash_flow: user-selected latest cash flow value (free cash flow or net income)
    """
    stock_id = get_stock_id(conn, ticker)

    if growth_rates:
        lo = float(growth_rates['conservative'])
        mean = float(growth_rates['moderate'])
        hi = float(growth_rates['optimistic'])
    else:
        growth_row = conn.execute(
            "SELECT growth_conservative, growth_moderate, growth_optimistic FROM analyst_growth_estimates WHERE stock_id=? ORDER BY estimate_date DESC LIMIT 1",
            (stock_id,)
        ).fetchone()
        if growth_row:
            lo = float(growth_row["growth_conservative"])
            mean = float(growth_row["growth_moderate"])
            hi = float(growth_row["growth_optimistic"])
        else:
            lo = mean = hi = 0.0

    if cash_flow is not None:
        latest_cf = cash_flow
    else:
        latest_cf = get_latest_cashflow_db(conn, stock_id)
    shares = get_shares_outstanding_db(conn, stock_id)
    rate = get_exchange_rate_for_ticker(conn, ticker)

    scenarios = {
        'conservative': lo,
        'moderate': mean,
        'optimistic': hi
    }
    ivs = {}
    for name, growth_rate in scenarios.items():
        total_pv = perform_finite_horizon_dcf(latest_cf, growth_rate, discount_rate, projection_years, margin_of_safety)
        per_share_value = total_pv / (shares * rate) if shares and rate else 0.0
        ivs[name] = per_share_value

    # Persist to DB (can choose only one type to persist)
    price_date = datetime.utcnow().strftime('%Y-%m-%d')
    persist_daily_finite_iv(conn, stock_id, price_date, ivs, projection_years, discount_rate, margin_of_safety)
    return ivs

@views.route('/dcf-finite-horizon', methods=['GET', 'POST'])
@login_required
def finite_horizon_dcf_form():
    session.pop('finite_horizon_applied_overrides', None)
    if request.method == 'POST':
        ticker = request.form.get('ticker')
        if ticker:
            session['finite_horizon_ticker'] = ticker
            # You might want to pull default growth rates here and set session['finite_horizon_growth_defaults']
            conn = get_financial_db_conn()
            stock_id = get_stock_id(conn, ticker)
            growth_rates = get_latest_analyst_growth_estimate(conn, stock_id)
            session['finite_horizon_growth_defaults'] = growth_rates
            session['finite_horizon_raw_displayName'] = ticker.upper()
            session.pop('finite_horizon_applied_overrides', None) 
    return render_template("finite_horizon_form.html", user=current_user)

@views.route('/dcf-finite-horizon/overrides', methods=['GET', 'POST'])
@login_required
def finite_horizon_overrides():
    ticker = session.get('finite_horizon_ticker')
    growth_defaults = session.get('finite_horizon_growth_defaults')
    raw_displayName = session.get('finite_horizon_raw_displayName', ticker.upper() if ticker else 'Selected Stock')

    if not ticker or not growth_defaults:
        flash("Session expired. Please start again.", "warning")
        return redirect(url_for("views.finite_horizon_dcf_form"))

    return render_template(
        "finite_horizon_overrides_form.html",
        user=current_user,
        ticker=ticker,
        raw_displayName=raw_displayName,
        defaults=growth_defaults,
        scenario_names=["Conservative", "Moderate", "Optimistic"]
    )


@views.route('/dcf-finite-horizon/calculate', methods=['GET', 'POST'])
@login_required
def finite_horizon_dcf_calculate():
    if request.method == 'POST':
        ticker = request.form.get('ticker')
        projection_years = request.form.get('projection_years')
        discount_rate = request.form.get('discount_rate')
        margin_of_safety = request.form.get('margin_of_safety')
        dcf_cashflow_type = request.form.get('dcf_cashflow_type', session.get('dcf_cashflow_type', 'fcf'))
    else:
        ticker = request.args.get('ticker') or session.get('finite_horizon_ticker')
        projection_years = request.args.get('projection_years')
        discount_rate = request.args.get('discount_rate')
        margin_of_safety = request.args.get('margin_of_safety')
        dcf_cashflow_type = request.args.get('dcf_cashflow_type') or session.get('dcf_cashflow_type', 'fcf')
    session['dcf_cashflow_type'] = dcf_cashflow_type

    if not ticker:
        flash('Ticker symbol is required.', 'error')
        return redirect(url_for('views.finite_horizon_dcf_form'))

    try:
        conn = get_financial_db_conn()
        stock_id = get_stock_id(conn, ticker)
        if not stock_id:
            flash('Stock not found in database.', 'error')
            return redirect(url_for('views.finite_horizon_dcf_form'))

        annual_income_data = get_income_statement_data(conn, stock_id, 'annual')
        latest_year_data = annual_income_data[-1] if annual_income_data else None
        latest_cf_fcf = latest_year_data.get("free_cash_flow") if latest_year_data else 0.0
        latest_cf_ni = latest_year_data.get("net_income") if latest_year_data else 0.0

        overrides = session.get('finite_horizon_applied_overrides')
        if overrides:
            growth_rates = {
                'conservative': overrides['growth_conservative'],
                'moderate': overrides['growth_moderate'],
                'optimistic': overrides['growth_optimistic']
            }
        else:
            growth_rates = get_latest_analyst_growth_estimate(conn, stock_id)

        dcf_negative_metrics = []
        if latest_year_data:
            fcf = latest_year_data.get("free_cash_flow")
            if fcf is not None and fcf < 0:
                dcf_negative_metrics.append(f"Free Cash Flow (latest year): {fcf:.2f}")
            ni = latest_year_data.get("net_income")
            if ni is not None and ni < 0:
                dcf_negative_metrics.append(f"Net Income (latest year): {ni:.2f}")
        if dcf_negative_metrics:
            flash(
                "⚠️ Warning: Negative metric(s) detected in DCF calculation that may result in negative IV value. Please review the following: "
                + ", ".join(dcf_negative_metrics),
                "warning"
            )

        cur = conn.cursor()
        iv_row = cur.execute("""
            SELECT price_date, iv_conservative, iv_moderate, iv_optimistic, projection_years, discount_rate, margin_of_safety
            FROM daily_finite_iv
            WHERE stock_id = ?
            ORDER BY price_date DESC LIMIT 1
        """, (stock_id,)).fetchone()

        projection_years = int(projection_years or (iv_row["projection_years"] if iv_row else 10))
        discount_rate = float(discount_rate or (iv_row["discount_rate"] if iv_row else 0.075))
        if discount_rate > 1.0:
            discount_rate = discount_rate / 100.0
        margin_of_safety = float(margin_of_safety or (iv_row["margin_of_safety"] if iv_row else 0.20))
        if margin_of_safety > 1.0:
            margin_of_safety = margin_of_safety / 100.0

        iv_fcf = get_finite_horizon_iv_and_persist(conn, ticker, projection_years, discount_rate, margin_of_safety, growth_rates, latest_cf_fcf)
        iv_ni = get_finite_horizon_iv_and_persist(conn, ticker, projection_years, discount_rate, margin_of_safety, growth_rates, latest_cf_ni)

        # Pick which result to show in JS and chart, based on user's choice
        result = iv_ni if dcf_cashflow_type == 'ni' else iv_fcf

        raw_displayName_row = conn.execute(
            "SELECT company_name FROM stocks WHERE id = ?", (stock_id,)
        ).fetchone()
        raw_displayName = raw_displayName_row["company_name"] if raw_displayName_row else ticker.upper()

        price_row = conn.execute(
            "SELECT close FROM daily_prices WHERE stock_id = ? ORDER BY price_date DESC LIMIT 1", (stock_id,)
        ).fetchone()
        current_price = price_row["close"] if price_row else None
        conn.close()

        result_df = pd.DataFrame([
            {"Scenario": k.capitalize(), "IV FCF": iv_fcf[k], "IV NI": iv_ni[k]} for k in iv_fcf.keys()
        ])
        result_table_html = result_df.to_html(classes="table table-hover", border=0)

        selected_scenario = request.args.get('comparison_scenario') or request.form.get('comparison_scenario') or 'moderate'
        scenarios_list = ["conservative", "moderate", "optimistic"]
        candlestick_json = []
        iv_line_json = []
        params_are_modified = (
            projection_years != 10 or discount_rate != 0.075 or margin_of_safety != 0.20
        )

        session['finite_horizon_ticker'] = ticker
        session['finite_horizon_growth_defaults'] = growth_rates
        session['finite_horizon_raw_displayName'] = raw_displayName

        summary = session.get('valuation_summary', [])
        selected_scenario = request.form.get('scenario') or request.args.get('scenario') or "moderate"
        price_val = price_row["close"] if price_row else None
        update_fields = {
            "model_type": "dcf",
            "dcf_iv_conservative_fcf": iv_fcf.get("conservative"),
            "dcf_iv_moderate_fcf": iv_fcf.get("moderate"),
            "dcf_iv_optimistic_fcf": iv_fcf.get("optimistic"),
            "dcf_iv_conservative_ni": iv_ni.get("conservative"),
            "dcf_iv_moderate_ni": iv_ni.get("moderate"),
            "dcf_iv_optimistic_ni": iv_ni.get("optimistic"),
            "margin_of_safety": margin_of_safety,
            "price": price_val,
            "updated": datetime.utcnow().strftime("%d/%m/%Y"),
        }
        summary = upsert_summary(summary, ticker, update_fields)
        session['valuation_summary'] = summary
        session.modified = True

        return render_template(
            "finite_horizon_result.html",
            user=current_user,
            ticker=ticker,
            raw_displayName=raw_displayName,
            result_table=result_table_html,
            current_price=current_price,
            selected_scenario=selected_scenario,
            result=result,  # <- this is the key, for |tojson in JS
            result_fcf=iv_fcf,
            result_ni=iv_ni,
            scenarios_list=scenarios_list,
            projection_years=projection_years,
            discount_rate=discount_rate,
            margin_of_safety=margin_of_safety,
            params_are_modified=params_are_modified,
            candlestick_json=candlestick_json,
            iv_line_json=iv_line_json,
            growth_rates=growth_rates,
            dcf_cashflow_type=dcf_cashflow_type
        )
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", 'error')
        current_app.logger.error(f"Unexpected Finite Horizon error for {ticker}: {e}", exc_info=True)
        return redirect(url_for('views.finite_horizon_dcf_form'))

@views.route('/dcf-finite-horizon/update-valuation', methods=['POST'])
@login_required
def update_finite_horizon_valuation():
    ticker = request.form.get('ticker')
    if not ticker:
        flash("Ticker symbol missing.", "error")
        return redirect(url_for('views.finite_horizon_dcf_form'))

    # Save overrides to session
    overrides = {
        'growth_conservative': float(request.form.get('growth_conservative')),
        'growth_moderate': float(request.form.get('growth_moderate')),
        'growth_optimistic': float(request.form.get('growth_optimistic'))
    }
    session['finite_horizon_applied_overrides'] = overrides

    # Forward to calculation
    return redirect(url_for('views.finite_horizon_dcf_calculate', ticker=ticker))


@views.route('/dcf-finite-horizon/reset-defaults', methods=['POST'])
@login_required
def reset_finite_horizon_defaults():
    ticker = request.form.get('ticker')
    if not ticker:
        flash('Ticker symbol is required for reset.', 'error')
        return redirect(url_for('views.finite_horizon_dcf_form'))
    try:
        conn = get_financial_db_conn()
        stock_id = get_stock_id(conn, ticker)
        if not stock_id:
            flash('Stock not found in database.', 'error')
            return redirect(url_for('views.finite_horizon_dcf_form'))

        growth_rates = get_latest_analyst_growth_estimate(conn, stock_id)
        annual_income_data = get_income_statement_data(conn, stock_id, 'annual')
        latest_year_data = annual_income_data[-1] if annual_income_data else None

        # Always default to free cash flow for reset
        dcf_cashflow_type = 'fcf'
        session['dcf_cashflow_type'] = dcf_cashflow_type
        if dcf_cashflow_type == 'ni':
            latest_cf = latest_year_data.get("net_income") if latest_year_data else 0.0
        else:
            latest_cf = latest_year_data.get("free_cash_flow") if latest_year_data else 0.0

        cur = conn.cursor()
        iv_row = cur.execute("""
            SELECT price_date, iv_conservative, iv_moderate, iv_optimistic, projection_years, discount_rate, margin_of_safety
            FROM daily_finite_iv
            WHERE stock_id = ?
            ORDER BY price_date DESC LIMIT 1
        """, (stock_id,)).fetchone()

        # Defaults if missing
        projection_years = 10
        discount_rate = 0.075
        margin_of_safety = 0.20

        # Calculate fresh IVs using defaults and persist
        iv = get_finite_horizon_iv_and_persist(conn, ticker, projection_years, discount_rate, margin_of_safety, growth_rates, latest_cf)

        raw_displayName_row = conn.execute(
            "SELECT company_name FROM stocks WHERE id = ?", (stock_id,)
        ).fetchone()
        raw_displayName = raw_displayName_row["company_name"] if raw_displayName_row else ticker.upper()

        price_row = conn.execute(
            "SELECT close FROM daily_prices WHERE stock_id = ? ORDER BY price_date DESC LIMIT 1", (stock_id,)
        ).fetchone()
        current_price = price_row["close"] if price_row else None
        conn.close()

        result_df = pd.DataFrame([
            {"Scenario": k.capitalize(), "Intrinsic Value": v} for k, v in iv.items()
        ])
        result_table_html = result_df.to_html(classes="table table-hover", border=0)

        selected_scenario = "moderate"
        scenarios_list = ["conservative", "moderate", "optimistic"]
        candlestick_json = []
        iv_line_json = []
        params_are_modified = False  # Defaults

        return render_template(
            "finite_horizon_result.html",
            user=current_user,
            ticker=ticker,
            raw_displayName=raw_displayName,
            result_table=result_table_html,
            current_price=current_price,
            selected_scenario=selected_scenario,
            result=iv,
            scenarios_list=scenarios_list,
            projection_years=projection_years,
            discount_rate=discount_rate,
            margin_of_safety=margin_of_safety,
            params_are_modified=params_are_modified,
            candlestick_json=candlestick_json,
            iv_line_json=iv_line_json,
            growth_rates=growth_rates,
            dcf_cashflow_type=dcf_cashflow_type
        )
    except Exception as e:
        flash(f"Error resetting to default: {e}", 'error')
        current_app.logger.error(f"Reset error for {ticker}: {e}", exc_info=True)
        return redirect(url_for('views.finite_horizon_dcf_form'))

def upsert_summary(summary, ticker, update_fields):
    found = False
    for row in summary:
        if row.get('ticker') == ticker:
            row.update({k: v for k, v in update_fields.items() if v is not None})
            found = True
            break
    if not found:
        blank_row = {
            "ticker": ticker,
            "model_type": None,
            "iv_wacc_conservative": None,
            "iv_wacc_moderate": None,
            "iv_wacc_optimistic": None,
            "iv_fixed_conservative": None,
            "iv_fixed_moderate": None,
            "iv_fixed_optimistic": None,
            "wacc_value": None,
            "required_return": None,
            "dcf_iv_conservative_fcf": None,
            "dcf_iv_moderate_fcf": None,
            "dcf_iv_optimistic_fcf": None,
            "dcf_iv_conservative_ni": None,
            "dcf_iv_moderate_ni": None,
            "dcf_iv_optimistic_ni": None,
            "margin_of_safety": None,
            "price": None,
            "pct": None,
            "status": None,
            "updated": None,
        }
        blank_row.update(update_fields)
        summary.append(blank_row)
    return summary

@views.route('/valuation-summary', methods=['GET', 'POST'])
@login_required
def valuation_summary():
    summary = session.get('valuation_summary', [])
    error_message = None

    # Always get scenario from POST or session
    selected_scenario = request.form.get('scenario') or session.get('selected_scenario', 'moderate')

    # If weightage form submitted (has wacc_weight and dcf_weight), update session
    if 'wacc_weight' in request.form and 'dcf_weight' in request.form:
        wacc_weight = float(request.form.get('wacc_weight', session.get('wacc_weight', 0.5)))
        dcf_weight = float(request.form.get('dcf_weight', session.get('dcf_weight', 0.5)))
        selected_wacc_type = request.form.get('wacc_type', session.get('selected_wacc_type', 'wacc'))
        selected_dcf_type = request.form.get('dcf_type', session.get('selected_dcf_type', 'fcf'))
        # Validate weights
        if round(wacc_weight + dcf_weight, 2) != 1.0:
            error_message = "Weights must sum to 1!"
            # DON'T update session, use previous session values
            wacc_weight = float(session.get('wacc_weight', 0.5))
            dcf_weight = float(session.get('dcf_weight', 0.5))
            selected_wacc_type = session.get('selected_wacc_type', 'wacc')
            selected_dcf_type = session.get('selected_dcf_type', 'fcf')
        else:
            session['wacc_weight'] = wacc_weight
            session['dcf_weight'] = dcf_weight
            session['selected_wacc_type'] = selected_wacc_type
            session['selected_dcf_type'] = selected_dcf_type
    else:
        wacc_weight = float(session.get('wacc_weight', 0.5))
        dcf_weight = float(session.get('dcf_weight', 0.5))
        selected_wacc_type = session.get('selected_wacc_type', 'wacc')
        selected_dcf_type = session.get('selected_dcf_type', 'fcf')

    session['selected_scenario'] = selected_scenario

    # ... summary construction/rendering unchanged ...
    patched_summary = []
    for row in summary:
        patched_row = dict(row)
        if selected_wacc_type == "wacc":
            wacc_iv = row.get(f"iv_wacc_{selected_scenario}")
        else:
            wacc_iv = row.get(f"iv_fixed_{selected_scenario}")
        dcf_iv = row.get(f"dcf_iv_{selected_scenario}_{selected_dcf_type}")

        weighted_iv = None
        if wacc_iv is not None and dcf_iv is not None:
            weighted_iv = wacc_weight * (wacc_iv or 0) + dcf_weight * (dcf_iv or 0)
        elif wacc_iv is not None:
            weighted_iv = wacc_iv
        elif dcf_iv is not None:
            weighted_iv = dcf_iv

        patched_row["weighted_iv"] = f"${weighted_iv:.2f}" if weighted_iv is not None else "N/A"

        price_val = row.get("price")
        pct = None
        status = None
        try:
            weighted_iv_num = float(str(weighted_iv).replace("$", "")) if weighted_iv is not None else None
            price_num = float(price_val) if price_val is not None else None
            if weighted_iv_num is not None and price_num is not None and price_num > 0:
                pct = ((weighted_iv_num - price_num) / price_num) * 100
                status = "Undervalued" if pct > 0 else "Overvalued"
        except Exception:
            pct = None
            status = None

        patched_row["pct"] = round(pct, 2) if pct is not None else None
        patched_row["status"] = status if status is not None else ""

        margin_val = row.get("margin_of_safety")
        if isinstance(margin_val, (float, int)):
            margin_display = f"{margin_val*100:.2f}%"
        elif isinstance(margin_val, str):
            margin_display = margin_val
        else:
            margin_display = ""

        req_return = f"{(row.get('wacc_value', 0)*100):.2f}%" if row.get('wacc_value') is not None else ""
        patched_row["required_return"] = req_return
        patched_row["margin_of_safety_display"] = margin_display
        patched_summary.append(patched_row)

    return render_template(
        "valuation_summary.html",
        valuation_summary=patched_summary,
        user=current_user,
        selected_scenario=selected_scenario,
        wacc_weight=wacc_weight,
        dcf_weight=dcf_weight,
        selected_wacc_type=selected_wacc_type,
        selected_dcf_type=selected_dcf_type,
        error_message=error_message
    )

@views.route('/valuation-summary/delete', methods=['POST'])
@login_required
def delete_ticker_from_summary():
    ticker = request.form.get('ticker')
    summary = session.get('valuation_summary', [])
    summary = [row for row in summary if row['ticker'] != ticker]
    session['valuation_summary'] = summary
    session.modified = True
    flash(f"Removed {ticker} from summary.", "success")
    return redirect(url_for('views.valuation_summary'))