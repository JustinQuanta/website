# website/calculator_finite_horizon.py
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from flask import current_app

class FiniteHorizonDCFCalculator:
    """
    Calculates the intrinsic value of a stock using a finite horizon
    Discounted Cash Flow (DCF) model, without a terminal value.
    """
    SCENARIO_CONSERVATIVE = "Conservative Scenario"
    SCENARIO_MODERATE = "Moderate Scenario"
    SCENARIO_OPTIMISTIC = "Optimistic Scenario"

    REGION_MAP_RATE = {
        'United States': 'USD', 'China': 'CNY', 'Hong Kong': 'HKD',
        'Japan': 'JPY', 'Singapore': 'SGD', 'Taiwan': 'TWD'
    }

    def __init__(self, ticker_symbol):
        """Initializes the calculator with the ticker and default parameters."""
        self.ticker = ticker_symbol.strip().upper()
        self.stock = yf.Ticker(self.ticker)
        self.stock_info = {}
        self.cases = [self.SCENARIO_CONSERVATIVE, self.SCENARIO_MODERATE, self.SCENARIO_OPTIMISTIC]

        # User-configurable parameters with defaults
        self.margin_of_safety = 0.20 # Default 20%
        self.projection_years = 10     # Default 10 years
        self.discount_rate = 0.075   # Default 7.50%

        # Data placeholders
        self.current_market_price = None
        self.balance = pd.DataFrame()
        self.financials = pd.DataFrame()
        self.cashflow = pd.DataFrame()
        self.growth_estimates = pd.DataFrame()

        # Currency attributes
        self.financials_reporting_currency = "USD"
        self.trading_currency = "USD"
        self.conversion_rate_from_financials_to_trading_currency = 1.0

        # Result attributes
        self.latest_cf = 0.0
        self.growth_data = {case: 0.0 for case in self.cases}
        self.result_df = pd.DataFrame()
        self.final_values_numeric = {case: 0.0 for case in self.cases}

        # Initial data fetch orchestration
        self._initialize_stock_data_and_currencies()
        self._calculate_growth_rates_and_fcf()

    # --- Data fetching and currency methods (adapted from WACC calculator) ---
    def _initialize_stock_data_and_currencies(self):
        if not self.is_valid_ticker():
            raise ValueError(f"Ticker {self.ticker} is invalid or data cannot be fetched.")
        self.fetch_current_price_and_currency()
        self._determine_currencies_and_conversion_rate()
        self.fetch_financial_data()

    def is_valid_ticker(self):
        try:
            if not self.stock_info or not self.stock_info.get('symbol'):
                self.stock_info = self.stock.info
                if not self.stock_info:
                    hist_check = self.stock.history(period="1d")
                    if not hist_check.empty: self.stock_info = self.stock.info
                    if not self.stock_info:
                        current_app.logger.warning(f"Ticker {self.ticker}: stock.info is empty after attempts.")
                        return False
            
            data = self.stock.history(period="1d")
            if data.empty and not self.stock_info.get('regularMarketPrice') and not self.stock_info.get("currentPrice"):
                current_app.logger.warning(f"Ticker {self.ticker} history is empty and no valid price in info.")
                return False
            
            fetched_symbol = self.stock_info.get('symbol', '')
            if fetched_symbol.upper() != self.ticker.upper():
                 current_app.logger.warning(f"Ticker validation mismatch: requested '{self.ticker}', yfinance info for '{fetched_symbol}'.")
                 return False
            return True
        except Exception as e:
            current_app.logger.error(f"Error during ticker validation for {self.ticker}: {e}", exc_info=False)
            return False

    def fetch_current_price_and_currency(self):
        try:
            if not self.stock_info: self.stock_info = self.stock.info
            self.trading_currency = self.stock_info.get('currency', 'USD').upper()
            
            price_local = self.stock_info.get('currentPrice') or self.stock_info.get('regularMarketPrice')
            if price_local is None:
                hist = self.stock.history(period="5d")
                if not hist.empty and 'Close' in hist.columns: price_local = hist['Close'].iloc[-1]
            
            if price_local is not None:
                self.current_market_price = float(price_local)
                current_app.logger.info(f"Fetched current price for {self.ticker}: {self.current_market_price} {self.trading_currency}")
            else:
                current_app.logger.warning(f"Could not retrieve current price for {self.ticker}.")
                self.current_market_price = None
        except Exception as e:
            current_app.logger.error(f"Error fetching current price for {self.ticker}: {e}", exc_info=True)
            self.current_market_price = None
            self.trading_currency = "USD"

    def _determine_currencies_and_conversion_rate(self):
        company_country = self.stock_info.get('country')
        self.financials_reporting_currency = self.REGION_MAP_RATE.get(company_country, self.trading_currency)
        current_app.logger.info(f"For {self.ticker}: Trading Currency = {self.trading_currency}, Determined Financials Currency = {self.financials_reporting_currency}")

        if self.financials_reporting_currency == self.trading_currency:
            self.conversion_rate_from_financials_to_trading_currency = 1.0
            return

        try:
            url = 'https://api.exchangerate-api.com/v4/latest/USD'
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            api_rates = response.json().get('rates', {})
            
            rate_fin = api_rates.get(self.financials_reporting_currency, 1.0)
            rate_trade = api_rates.get(self.trading_currency, 1.0)

            if rate_fin != 0:
                self.conversion_rate_from_financials_to_trading_currency = rate_trade / rate_fin
                current_app.logger.info(f"Conversion rate from {self.financials_reporting_currency} to {self.trading_currency} set to {self.conversion_rate_from_financials_to_trading_currency:.4f}")
            else:
                current_app.logger.warning("Financials currency rate is zero. Using 1.0 for conversion.")
                self.conversion_rate_from_financials_to_trading_currency = 1.0
        except Exception as e:
            current_app.logger.error(f"Could not fetch exchange rates. Using 1.0 for conversion. Error: {e}")
            self.conversion_rate_from_financials_to_trading_currency = 1.0

    def fetch_financial_data(self):
        current_app.logger.info(f"Fetching financial statements for '{self.ticker}'.")
        self.balance = self.stock.balance_sheet
        self.financials = self.stock.financials
        self.cashflow = self.stock.cashflow
        try:
            self.growth_estimates = self.stock.get_growth_estimates()
        except AttributeError:
            self.growth_estimates = self.stock.growth_estimates # Fallback for older yfinance versions

        if self.cashflow.empty:
            raise ValueError(f"Cashflow statement for {self.ticker} is empty. Cannot proceed.")

    # --- Core logic from your script (UNCHANGED as requested) ---
    def _calculate_growth_rates_and_fcf(self):
        if 'Free Cash Flow' in self.cashflow.index and not self.cashflow.loc['Free Cash Flow'].isnull().all():
            self.latest_cf = self.cashflow.loc['Free Cash Flow'].dropna().iloc[0]
        else:
            current_app.logger.warning("Free Cash Flow not available. Falling back to Net Income.")
            if 'Net Income' in self.cashflow.index and not self.cashflow.loc['Net Income'].isnull().all():
                self.latest_cf = self.cashflow.loc['Net Income'].dropna().iloc[0]
            else:
                raise ValueError("Both Free Cash Flow and Net Income are unavailable.")
        
        if self.latest_cf <= 0:
            current_app.logger.warning(f"Latest Cash Flow used is non-positive ({self.latest_cf:,.2f}). DCF will be negative.")
        current_app.logger.info(f"Latest Cash Flow for projection: {self.latest_cf:,.2f} {self.financials_reporting_currency}")

        try:
            growth_next_year = self.growth_estimates['stockTrend'].iloc[2]
            growth_next_5_years = self.growth_estimates['stockTrend'].iloc[3]
            
            equity = self.balance.loc['Stockholders Equity'].dropna()
            if len(equity) > 1:
                equity_growth = (equity.iloc[0] / equity.iloc[-1])**(1 / (len(equity) - 1)) - 1
            else:
                equity_growth = 0.0
                current_app.logger.warning("Not enough equity data to calculate historical growth. Using 0.")

            bear, base, bull = self._analyze_numbers(growth_next_year, growth_next_5_years, equity_growth)
            self.growth_data[self.cases[0]] = bear
            self.growth_data[self.cases[1]] = base
            self.growth_data[self.cases[2]] = bull
        except (IndexError, KeyError):
            current_app.logger.warning("Could not retrieve analyst growth estimates. Using 0% for growth rates.")
            self.growth_data = {case: 0.0 for case in self.cases}

    def _analyze_numbers(self, a, b, c):
        numbers = [float(a), float(b), float(c)]
        minimum = min(numbers)
        maximum = max(numbers)
        average = sum(numbers) / len(numbers)
        return minimum, average, maximum

    def _perform_finite_dcf(self):
        total_present_values = {case: 0.0 for case in self.cases}
        for case_name in self.cases:
            projected_cf_list = []
            growth_rate = self.growth_data[case_name]
            
            for i in range(self.projection_years):
                year = i + 1
                cf_this_year = self.latest_cf * ((1 + growth_rate) ** year) # Corrected compounding
                projected_cf_list.append(cf_this_year)
                discounted_val = cf_this_year / ((1 + self.discount_rate) ** year)
                total_present_values[case_name] += discounted_val
        return pd.Series(total_present_values)

    # --- Main calculation and update methods ---
    def calculate_intrinsic_value(self):
        total_equity_values_financials_ccy = self._perform_finite_dcf()
        shares = self.stock_info.get('sharesOutstanding')
        if not shares or shares == 0:
            raise ValueError("Shares outstanding is zero or None.")

        per_share_financials_ccy = total_equity_values_financials_ccy / shares
        per_share_trading_ccy = per_share_financials_ccy * self.conversion_rate_from_financials_to_trading_currency
        
        
        final_value_per_share = per_share_trading_ccy * (1 - self.margin_of_safety)

        # Store raw numeric values before applying margin of safety        
        self.final_values_numeric = (final_value_per_share).to_dict()
        
        # --- Format Output ---
        currency_symbol = self.stock_info.get('currency', '$')

        value_df = pd.DataFrame(final_value_per_share).T
        value_df.index = [f'Intrinsic Value ({currency_symbol})']
        
        growth_df = pd.DataFrame(self.growth_data, index=['Annual Growth Rate']).T.reindex(self.cases).T

        self.result_df = pd.concat([value_df, growth_df])
        self.result_df.loc['Annual Growth Rate'] = self.result_df.loc['Annual Growth Rate'].map('{:.2%}'.format)
        self.result_df.loc[f'Intrinsic Value ({currency_symbol})'] = self.result_df.loc[f'Intrinsic Value ({currency_symbol})'].map('{:,.2f}'.format)
        
        return self.result_df

    def get_numeric_iv_for_comparison(self, scenario_name):
        return self.final_values_numeric.get(scenario_name)

    def update_margin_of_safety(self, margin_pct):
        if 0 <= margin_pct < 100: self.margin_of_safety = margin_pct / 100.0
        else: current_app.logger.warning(f"Invalid margin of safety {margin_pct}. Using default.")

    def update_projection_years(self, years):
        if years > 0: self.projection_years = int(years)
        else: current_app.logger.warning(f"Invalid projection years {years}. Using default.")

    def update_discount_rate(self, rate_pct):
        if 0 < rate_pct < 100: self.discount_rate = rate_pct / 100.0
        else: current_app.logger.warning(f"Invalid discount rate {rate_pct}. Using default.")

