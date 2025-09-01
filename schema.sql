-- schema.sql (v2)
-- Adds an 'exchange_timezone' column to the stocks table.

CREATE TABLE IF NOT EXISTS stocks (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    company_name TEXT,
    currency TEXT,
    exchange TEXT,
    exchange_timezone TEXT,
    country TEXT,
    sector TEXT,
    industry TEXT,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_prices (
    id INTEGER PRIMARY KEY,
    stock_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    shares_outstanding BIGINT,
    market_cap BIGINT,
    beta REAL,
    total_debt BIGINT,
    UNIQUE(stock_id, price_date),
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

CREATE TABLE IF NOT EXISTS annual_financials (
    id INTEGER PRIMARY KEY,
    stock_id INTEGER NOT NULL,
    report_year INTEGER NOT NULL,
    total_revenue BIGINT,
    net_income BIGINT,
    interest_expense BIGINT,
    other_interest_expense BIGINT,
    pretax_income BIGINT,
    tax_provision BIGINT,
    total_stockholder_equity BIGINT,
    long_term_debt BIGINT,
    long_term_debt_and_capital_lease_obligation BIGINT,
    current_debt BIGINT,
    other_current_borrowings BIGINT,
    free_cash_flow BIGINT,
    UNIQUE(stock_id, report_year),
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

CREATE TABLE IF NOT EXISTS quarterly_financials (
    id INTEGER PRIMARY KEY,
    stock_id INTEGER NOT NULL,
    report_date DATE NOT NULL,
    total_revenue BIGINT,
    net_income BIGINT,
    interest_expense BIGINT,
    other_interest_expense BIGINT,
    pretax_income BIGINT,
    tax_provision BIGINT,
    total_stockholder_equity BIGINT,
    long_term_debt BIGINT,
    long_term_debt_and_capital_lease_obligation BIGINT,
    current_debt BIGINT,
    other_current_borrowings BIGINT,
    free_cash_flow BIGINT,
    UNIQUE(stock_id, report_date),
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

CREATE TABLE IF NOT EXISTS global_market_data (
    id INTEGER PRIMARY KEY,
    data_name TEXT NOT NULL UNIQUE, -- e.g., 'risk_free_rate_tnx'
    data_value REAL,
    last_updated DATETIME
);

CREATE TABLE IF NOT EXISTS daily_market_data (
    id INTEGER PRIMARY KEY,
    data_date DATE NOT NULL,
    data_name TEXT NOT NULL,
    data_value REAL,
    UNIQUE(data_date, data_name)
);

CREATE TABLE IF NOT EXISTS tracked_tickers (
    id INTEGER PRIMARY KEY,
    ticker_symbol TEXT NOT NULL UNIQUE,
    market TEXT NOT NULL,           -- e.g., 'US', 'HK', 'SG' for categorization
    is_active INTEGER DEFAULT 1,    -- A flag to easily enable/disable tracking (1=active, 0=inactive)
    date_added DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Revenue Estimates (per ticker per date)
CREATE TABLE IF NOT EXISTS revenue_estimates (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL,
  estimate_date DATE NOT NULL,
  estimate_type TEXT NOT NULL,  -- 'absolute_current_year', 'absolute_next_year', 'growth_current_year', 'growth_next_year'
  value REAL,
  UNIQUE(stock_id, estimate_date, estimate_type),
  FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

CREATE TABLE IF NOT EXISTS analyst_growth_estimates (
    id INTEGER PRIMARY KEY,
    stock_id INTEGER NOT NULL,
    estimate_date DATE NOT NULL,
    growth_next_year REAL,
    growth_next_5_years REAL,
    equity_growth REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_id, estimate_date),
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

-- Store computed annual ratios per stock per year
CREATE TABLE IF NOT EXISTS annual_ratios (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL,
  report_year INTEGER NOT NULL,
  fcfe_ratio REAL,
  ni_margin REAL,
  revenue_growth REAL,
  UNIQUE(stock_id, report_year),
  FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

-- Store computed daily WACC per stock per date
CREATE TABLE IF NOT EXISTS daily_wacc (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL,
  price_date DATE NOT NULL,
  wacc_value REAL NOT NULL,
  UNIQUE(stock_id, price_date),
  FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

-- Store calculated daily Intrinsic Values per stock per date & scenario
CREATE TABLE IF NOT EXISTS daily_iv (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER NOT NULL,
  price_date DATE NOT NULL,
  iv_conservative REAL,
  iv_moderate     REAL,
  iv_optimistic   REAL,
  iv_conservative_fixed REAL,
  iv_moderate_fixed REAL,
  iv_optimistic_fixed REAL,
  UNIQUE(stock_id, price_date),
  FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

CREATE TABLE IF NOT EXISTS daily_finite_iv (
    id INTEGER PRIMARY KEY,
    stock_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    iv_conservative REAL,
    iv_moderate REAL,
    iv_optimistic REAL,
    projection_years INTEGER,
    discount_rate REAL,
    margin_of_safety REAL,
    UNIQUE(stock_id, price_date),
    FOREIGN KEY(stock_id) REFERENCES stocks(id)
);

CREATE TABLE IF NOT EXISTS latest_parameters (
    stock_id INTEGER,
    price_date TEXT,
    growth_lo REAL,
    growth_md REAL,
    growth_hi REAL,
    fcfe_lo REAL,
    fcfe_md REAL,
    fcfe_hi REAL,
    margin_lo REAL,
    margin_md REAL,
    margin_hi REAL,
    PRIMARY KEY (stock_id, price_date)
);
