import streamlit as st
import numpy as np
import pandas as pd
import altair as alt
from collections import defaultdict
import google.generativeai as genai
import io

st.set_page_config(page_title="Wealth & Retirement Forecaster", layout="wide")

st.title("📈 Wealth & Retirement Forecaster")
st.caption("Plan your financial future by simulating life events, market crashes, and property purchases.")

# --- Initialize Session States ---
if "events" not in st.session_state:
    st.session_state.events = []
if "custom_spending" not in st.session_state:
    st.session_state.custom_spending = []
if "simulation_run" not in st.session_state:
    st.session_state.simulation_run = False

# ==========================================
# 1. SIDEBAR: Profile & Asset Starting Point
# ==========================================
st.sidebar.header("1. Your Basic Profile (Start Here)")
col_sb1, col_sb2 = st.sidebar.columns(2)
with col_sb1:
    current_age = st.number_input("Current Age", min_value=18, max_value=80, value=30)
    current_salary = st.number_input("Monthly Gross Salary ($)", min_value=0, value=6000, step=250)
    salary_growth = st.number_input("Salary Growth (% p.a.)", min_value=0.0, max_value=20.0, value=3.0, step=0.5) / 100
with col_sb2:
    target_age = st.number_input("Target Age", min_value=current_age + 1, max_value=100, value=45)
    target_nw = st.number_input("Net Worth Target ($)", min_value=0, value=1_000_000, step=50_000)
    inflation_rate = st.number_input("Inflation (% p.a.)", min_value=0.0, max_value=15.0, value=2.5, step=0.25) / 100

residency = st.sidebar.selectbox("Work Status in Singapore (Determines CPF)", ["Singaporean/PR (CPF)", "WP/EP/SP (No CPF)"])

include_cpf_in_nw = False
if residency == "Singaporean/PR (CPF)":
    include_cpf_in_nw = st.sidebar.checkbox("Include CPF in Target Net Worth", value=True)

st.sidebar.header("2. Current Savings & Investments")
cash_balance = st.sidebar.number_input("Liquid Cash / Emergency Fund ($)", min_value=0, value=0, step=5000)
invested_balance = st.sidebar.number_input("Invested Portfolio (Stocks/ETFs) ($)", min_value=0, value=0, step=5000)

cpf_oa, cpf_sa, cpf_ma = 0, 0, 0
if residency == "Singaporean/PR (CPF)" and include_cpf_in_nw:
    st.sidebar.markdown("##### CPF Balances")
    cpf_oa = st.sidebar.number_input("Ordinary Account (OA) ($)", min_value=0, value=0, step=5000)
    cpf_sa = st.sidebar.number_input("Special Account (SA) ($)", min_value=0, value=0, step=5000)
    cpf_ma = st.sidebar.number_input("Medisave Account (MA) ($)", min_value=0, value=0, step=5000)
    
# ==========================================
# 2. BUDGET INGESTION & CASHFLOW
# ==========================================
st.header("3. Estimated Monthly Budget Cost")

tab_manual, tab_upload = st.tabs(["Manual Monthly Budget", "Upload Excel Template"])

with tab_manual:
    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        rent_mortgage = st.number_input("Rent / Mortgage ($)", min_value=0, value=1200, step=50)
        food_bev = st.number_input("Food & Dining ($)", min_value=0, value=650, step=50)
    with col_b2:
        utilities_bills = st.number_input("Utilities & Telco ($)", min_value=0, value=180, step=20)
        transport_travel = st.number_input("Transport & Travel ($)", min_value=0, value=350, step=50)
    with col_b3:
        entertainment = st.number_input("Discretionary / Leisure ($)", min_value=0, value=250, step=50)
        other_exp = st.number_input("Insurance ($)", min_value=0, value=300, step=50)
    
    with st.expander("➕ Add Extra Monthly Expenses (Optional)", expanded=False):
        col_c1, col_c2, col_c3 = st.columns([2, 2, 1])
        with col_c1:
            new_cat_name = st.text_input("Category Name", value="Gym & Subscriptions")
        with col_c2:
            new_cat_amt = st.number_input("Monthly Cost ($)", min_value=0, value=100, step=50)
        with col_c3:
            st.write("")
            st.write("")
            if st.button("Add Category"):
                st.session_state.custom_spending.append({
                    "Name": new_cat_name,
                    "Amount": new_cat_amt
                })
                st.rerun()

    custom_spending_total = 0
    if st.session_state.custom_spending:
        st.markdown("#### Your Custom Categories")
        for i, cat in enumerate(st.session_state.custom_spending):
            col_cs1, col_cs2 = st.columns([5, 1])
            col_cs1.write(f"**{cat['Name']}** ➔ ${cat['Amount']:,.2f}")
            if col_cs2.button("❌ Remove", key=f"del_cat_{i}"):
                st.session_state.custom_spending.pop(i)
                st.rerun()
                
        st.write("") 
        if st.button("🗑️ Clear All Categories"):
            st.session_state.custom_spending = []
            st.rerun()
            
        custom_spending_total = sum(item["Amount"] for item in st.session_state.custom_spending)

    baseline_monthly_expenses = rent_mortgage + food_bev + utilities_bills + transport_travel + entertainment + other_exp + custom_spending_total

with tab_upload:
    st.info("Template columns required: `Month_Year`, `Category`, `Amount`, `Cashflow_Type` (Inflow / Outflow)")
    uploaded_file = st.file_uploader("Upload Budget Excel (.xlsx)", type=["xlsx"])
    if uploaded_file:
        try:
            df_budget = pd.read_excel(uploaded_file)
            req_cols = {"Month_Year", "Category", "Amount", "Cashflow_Type"}
            if req_cols.issubset(df_budget.columns):
                outflows = df_budget[df_budget['Cashflow_Type'].str.strip().str.lower() == 'outflow']['Amount'].sum()
                st.success(f"File verified! Detected monthly outflows: ${outflows:,.2f}")
                baseline_monthly_expenses = outflows
            else:
                st.error(f"Missing required columns. Schema must contain: {req_cols}")
        except Exception as e:
            st.error(f"Error reading file: {e}")

# --- Dynamic Take-Home Pay ---
if residency == "Singaporean/PR (CPF)":
    ow_subject_to_cpf = min(current_salary, 8000)
    employee_cpf_deduction = ow_subject_to_cpf * 0.20
    take_home_pay = current_salary - employee_cpf_deduction
else:
    take_home_pay = current_salary

# --- MOVED OUTSIDE TABS: Investment Strategy UI ---
st.markdown("##### 📈 Monthly Savings & Investing Plan")
inv_strategy = st.radio(
    "How do you want to allocate your surplus cash?",
    ["Fixed Amount ($)", "Percentage of Surplus Cash (%)", "Aggressive Sweep (Invest 100% of leftover cash)"],
    help="This scales your investments based on the surplus cash generated after baseline expenses."
)

monthly_investment = 0
target_pct = 0.0
projected_monthly_inv_ui = 0 # Used strictly for the UI metric display

current_monthly_surplus = take_home_pay - baseline_monthly_expenses

if inv_strategy == "Fixed Amount ($)":
    monthly_investment = st.number_input("Amount to Invest in Stocks Monthly ($)", min_value=0, value=1000, step=100)
    projected_monthly_inv_ui = monthly_investment
elif inv_strategy == "Percentage of Surplus Cash (%)":
    target_pct = st.slider("Percentage of Surplus Cash to Invest (%)", min_value=0, max_value=100, value=50, step=1) / 100
    projected_monthly_inv_ui = max(0, current_monthly_surplus * target_pct)
else:
    # Aggressive Sweep: Everything leftover goes to market
    projected_monthly_inv_ui = max(0, current_monthly_surplus) 

total_outflow_ui = baseline_monthly_expenses + projected_monthly_inv_ui
surplus_cash_ui = take_home_pay - total_outflow_ui

st.write("---")
col_met1, col_met2, col_met3 = st.columns(3)
col_met1.metric("Take-Home Pay (After CPF)", f"${take_home_pay:,.2f}")
col_met2.metric("Total Baseline Expenses", f"${baseline_monthly_expenses:,.2f}")
col_met3.metric("Projected Monthly Invest", f"${projected_monthly_inv_ui:,.2f}")

valid_budget = True
if current_monthly_surplus < 0:
    st.error(f"🚨 **Budget Deficit Detected!** Your baseline expenses exceed your take-home pay. The simulation cannot run with a negative baseline cash flow.")
    valid_budget = False
elif surplus_cash_ui > 0 and inv_strategy != "Aggressive Sweep (Invest all leftover cash)":
    st.info(f"💡 **Uninvested Cash:** You currently have an estimated **${surplus_cash_ui:,.2f}** left over every month. This money will automatically be swept into your liquid Bank/Emergency fund (growing at your safe cash yield rate).")

# ==========================================
# 3. MILESTONES & STRESS-TEST EVENTS
# ==========================================
st.header("4. Plan for Big Life Events (Optional)")

with st.expander("➕ Add an Event (Optional: House, Job Loss, Crash)", expanded=False):
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        ev_year = st.number_input("Event Year", min_value=2026, max_value=2065, value=2028)
    with col_e2:
        ev_type = st.selectbox("Event Category", [
            "Market Crash (Stock Market Drop)", 
            "Property Purchase", 
            "Job Loss / Career Break",
            "Large One-Off Purchase (Wedding, Car, etc.)"
        ])
        
    if ev_type == "Property Purchase":
        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1:
            prop_val = st.number_input("Property Value (Today's $)", min_value=0, value=600000, step=10000)
            dp_pct = st.slider("Downpayment (%)", min_value=0, max_value=100, value=25)
        with col_p2:
            mortgage_rate = st.number_input("Mortgage Rate (%)", min_value=0.0, value=2.5, step=0.1)
            tenure = st.number_input("Loan Tenure (Years)", min_value=1, max_value=35, value=25)
        with col_p3:
            st.info("💡 Monthly mortgage will be auto-calculated.")
            auto_replace_rent = st.checkbox("Stop paying baseline rent after purchase?", value=True)
            mortgage_funding = st.radio("Fund Downpayment & Mortgage with:", ["CPF OA (Spills to Cash if short)", "100% Cash"])
            use_cpf_prop = True if "CPF OA" in mortgage_funding else False
            st.write("")
            if st.button("Add Property Event"):
                st.session_state.events.append({
                    "Year": ev_year, "Type": ev_type, "PropVal": prop_val, 
                    "DP_Pct": dp_pct, "Rate": mortgage_rate, "Tenure": tenure, 
                    "AutoReplaceRent": auto_replace_rent, "UseCPF": use_cpf_prop
                })
                st.rerun()
    else:
        col_n1, col_n2 = st.columns([3, 1])
        with col_n1:
            if ev_type == "Market Crash (Stock Market Drop)":
                ev_val = st.slider("Equity Drawdown (%)", min_value=-60, max_value=-5, value=-25, step=5)
            elif ev_type == "Large One-Off Purchase (Wedding, Car, etc.)":
                ev_val = st.number_input("Outflow (Today's $)", min_value=0, value=30000, step=1000)
            else:
                ev_val = st.slider("Duration without Income (Months)", min_value=1, max_value=12, value=4)
        with col_n2:
            st.write("")
            st.write("")
            if st.button("Add Event"):
                st.session_state.events.append({
                    "Year": ev_year,
                    "Type": ev_type,
                    "Magnitude": ev_val
                })
                st.rerun()

if st.session_state.events:
    st.markdown("#### Your Scheduled Events")
    
    for i, event in enumerate(st.session_state.events):
        col_ev1, col_ev2 = st.columns([5, 1])
        
        if event["Type"] == "Market Crash (Stock Market Drop)":
            val_display = f"{event['Magnitude']}% Drop"
        elif event["Type"] == "Property Purchase":
            val_display = f"${event['PropVal']:,} Property ({event['DP_Pct']}% DP, {event['Tenure']} Yr Loan)"
        elif event["Type"] == "Large One-Off Purchase (Wedding, Car, etc.)":
            val_display = f"${event['Magnitude']:,}"
        else:
            val_display = f"{event['Magnitude']} Months"
            
        col_ev1.write(f"**{event['Year']}** | {event['Type']} ➔ {val_display}")
        
        if col_ev2.button("❌ Remove", key=f"del_event_{i}"):
            st.session_state.events.pop(i)
            st.rerun()
            
    st.write("") 
    if st.button("🗑️ Clear All Events"):
        st.session_state.events = []
        st.rerun()

# ==========================================
# 4. SIMULATION ENGINE (Monte Carlo + Baseline)
# ==========================================
st.header("5. Market Assumptions & Simulation")

col_m1, col_m2, col_m3 = st.columns(3)
with col_m1:
    expected_market_return = st.number_input("Expected Equity Return (% p.a.)", value=10.5, step=0.5) / 100
with col_m2:
    market_volatility = st.number_input("Expected Market Swings / Volatility (% per year)", min_value=0.0, value=20.0, step=1.0) / 100
with col_m3:
    cash_yield = st.number_input("Cash Yield / Bank Rate (% p.a.)", min_value=0.0, value=1.5, step=0.1) / 100

st.subheader("Random Market Crashes (Stress Test)")
col_r1, col_r2, col_r3, col_r4 = st.columns(4)
with col_r1:
    enable_random_recessions = st.checkbox("Enable Random Market Crashes", value=True)
with col_r2:
    recession_prob = st.slider("Annual Crash Probability (%)", min_value=1, max_value=25, value=10) / 100
with col_r3:
    max_drawdown = st.slider("Maximum Drawdown Limit (%)", min_value=-80, max_value=-15, value=-40) / 100
with col_r4:
    recovery_multiplier = st.slider("Market Bounce-Back (Multiplier after a crash)", min_value=1.0, max_value=3.0, value=1.5, step=0.1)

with st.expander("⚙️ Advanced Engine Settings (Tune Iterations)"):
    sim_iterations = st.number_input("Monte Carlo Iterations", min_value=10000, max_value=500000, value=50000, step=10000)
    st.caption("Note: Running 100,000+ paths provides extreme statistical accuracy but may take a few seconds to process.")

st.subheader("How do you want to view your results?")
display_mode = st.radio("Display Projection In:", ["Future Value (Without factoring in inflation)", "Today's Value (Adjusted for inflation)"], horizontal=True)

# Helper for CPF Rates
def get_cpf_allocation(age):
    if age <= 35: return 0.23, 0.06, 0.08, 0.20
    elif age <= 45: return 0.21, 0.07, 0.09, 0.20
    elif age <= 50: return 0.19, 0.08, 0.10, 0.20
    elif age <= 55: return 0.15, 0.115, 0.105, 0.20
    else: return 0.12, 0.09, 0.105, 0.15 # Simplified >= 55

if st.button("🚀 Execute Simulation", type="primary", disabled=not valid_budget):
    
    with st.spinner(f"Computing timelines with multi-tranche CPF sweeps..."):
        horizon_years = target_age - current_age
        years_array = np.arange(2026, 2026 + horizon_years + 1)
        ages_array = np.arange(current_age, target_age + 1)
        num_steps = len(years_array)
    
    capex_dict, recession_dict, jobloss_dict = defaultdict(float), {}, defaultdict(int)
    property_events = defaultdict(list)
    
    for e in st.session_state.events:
        if e["Type"] == "Large One-Off Purchase (Wedding, Car, etc.)": capex_dict[e["Year"]] += e["Magnitude"]
        elif e["Type"] == "Market Crash (Stock Market Drop)": recession_dict[e["Year"]] = e["Magnitude"] / 100
        elif e["Type"] == "Job Loss / Career Break": jobloss_dict[e["Year"]] += e["Magnitude"]
        elif e["Type"] == "Property Purchase": property_events[e["Year"]].append(e)

    all_trajectories = np.zeros((sim_iterations, num_steps))
    cpf_only_trajectories = np.zeros((sim_iterations, num_steps))
    
    all_cash_paths = np.zeros((sim_iterations, num_steps))
    all_invest_paths = np.zeros((sim_iterations, num_steps))
    all_cpf_paths = np.zeros((sim_iterations, num_steps))
    
    initial_total_nw = cash_balance + invested_balance + (cpf_oa + cpf_sa + cpf_ma if include_cpf_in_nw else 0)
    all_trajectories[:, 0] = initial_total_nw
    
    salary_growth_array = np.zeros(num_steps)
    salary_growth_array[0] = salary_growth
    peak_age = 45
    decay_rate = 0.005 # Wage growth drops by 0.5% per year after peak
    
    for t in range(1, num_steps):
        if ages_array[t] > peak_age:
            # Decay linearly until it hits the inflation floor
            salary_growth_array[t] = max(inflation_rate, salary_growth_array[t-1] - decay_rate)
        else:
            salary_growth_array[t] = salary_growth_array[t-1]
            
    for sim in range(sim_iterations):
        curr_cash, curr_invest = cash_balance, invested_balance
        curr_oa, curr_sa, curr_ma, curr_ra = cpf_oa, cpf_sa, cpf_ma, 0
        curr_sal, curr_exp = current_salary, baseline_monthly_expenses
        recovering_from_crash, ra_created = False, False
        active_mortgage, active_mortgage_cpf = 0, 0 

        for t in range(1, num_steps):
            sim_year, sim_age = years_array[t], ages_array[t]
            curr_sal *= (1 + salary_growth_array[t])
            curr_exp *= (1 + inflation_rate)
            
            # --- FIX: Deduct Capex Early (Before Market Volatility) ---
            if sim_year in capex_dict:
                capex = capex_dict[sim_year] * ((1 + inflation_rate) ** (sim_year - 2026))
                if curr_cash >= capex: 
                    curr_cash -= capex
                else: 
                    curr_invest = max(0, curr_invest - (capex - curr_cash))
                    curr_cash = 0
            
            # --- FIX: Strict Property Capital Waterfall ---
            if sim_year in property_events:
                for p_event in property_events[sim_year]:
                    inflated_prop = p_event["PropVal"] * ((1 + inflation_rate) ** (sim_year - 2026))
                    dp_amount = inflated_prop * (p_event["DP_Pct"] / 100)
                    loan_amount = inflated_prop - dp_amount
                    
                    r, n = (p_event["Rate"] / 100) / 12, p_event["Tenure"] * 12
                    monthly_mortgage = loan_amount * (r * (1 + r)**n) / ((1 + r)**n - 1) if r>0 else loan_amount/n
                    
                    property_failed = False
                    if p_event.get("UseCPF", False):
                        if curr_oa >= dp_amount:
                            temp_oa = dp_amount; temp_cash = 0; temp_inv = 0
                        else:
                            temp_oa = curr_oa
                            shortfall = dp_amount - curr_oa
                            if curr_cash >= shortfall:
                                temp_cash = shortfall; temp_inv = 0
                            else:
                                temp_cash = curr_cash; temp_inv = shortfall - curr_cash
                                
                        if temp_inv > curr_invest:
                            property_failed = True # Cannot afford
                        else:
                            curr_oa -= temp_oa; curr_cash -= temp_cash; curr_invest -= temp_inv
                            active_mortgage_cpf += monthly_mortgage
                    else:
                        if curr_cash >= dp_amount:
                            temp_cash = dp_amount; temp_inv = 0
                        else:
                            temp_cash = curr_cash; temp_inv = dp_amount - curr_cash
                            
                        if temp_inv > curr_invest:
                            property_failed = True # Cannot afford
                        else:
                            curr_cash -= temp_cash; curr_invest -= temp_inv
                            active_mortgage += monthly_mortgage
                    
                    if not property_failed and p_event.get("AutoReplaceRent", False):
                        curr_exp = max(0, curr_exp - (rent_mortgage * ((1 + inflation_rate) ** (sim_year - 2026))))

            months_worked = max(0, 12 - jobloss_dict.get(sim_year, 0))
                
            if residency == "Singaporean/PR (CPF)":
                ow_capped = min(curr_sal, 8000)
                alloc_oa, alloc_sa, alloc_ma, emp_deduct = get_cpf_allocation(sim_age)
                
                curr_oa += (ow_capped * alloc_oa) * months_worked
                sa_contrib = (ow_capped * alloc_sa) * months_worked
                ma_contrib = (ow_capped * alloc_ma) * months_worked
                annual_takehome = (curr_sal - (ow_capped * emp_deduct)) * months_worked
                
                base_bhs_2026 = 71500
                years_to_lock = min(sim_year, 2026 + max(0, 65 - current_age)) - 2026
                current_bhs = base_bhs_2026 * ((1 + inflation_rate) ** years_to_lock)
                
                if curr_ma + ma_contrib > current_bhs:
                    overflow = (curr_ma + ma_contrib) - current_bhs
                    curr_ma = current_bhs  
                    if sim_age < 55: curr_sa += sa_contrib + overflow
                    else: curr_sa += sa_contrib; curr_ra += overflow
                else:
                    curr_ma += ma_contrib; curr_sa += sa_contrib
                
                annual_cpf_mort = active_mortgage_cpf * 12
                if curr_oa >= annual_cpf_mort: curr_oa -= annual_cpf_mort
                else:
                    spillover = annual_cpf_mort - curr_oa
                    curr_oa = 0; curr_cash -= spillover
                    
                curr_oa *= 1.025; curr_sa *= 1.04; curr_ma *= 1.04; curr_ra *= 1.04
                
                combined_bal = curr_oa + curr_sa + curr_ma + curr_ra
                bonus_base = min(combined_bal, 60000)
                smr_base = min(curr_sa + curr_ma + curr_ra, bonus_base)
                oa_base = min(curr_oa, 20000, bonus_base - smr_base)
                
                curr_oa += oa_base * 0.01
                if smr_base > 0:
                    curr_sa += (curr_sa / (curr_sa + curr_ma + curr_ra)) * smr_base * 0.01
                    curr_ma += (curr_ma / (curr_sa + curr_ma + curr_ra)) * smr_base * 0.01
                    curr_ra += (curr_ra / (curr_sa + curr_ma + curr_ra)) * smr_base * 0.01
                
                if sim_age >= 55 and not ra_created:
                    frs_limit = 220000 * ((1 + inflation_rate) ** t)
                    sa_sweep = min(curr_sa, frs_limit)
                    curr_sa -= sa_sweep; curr_ra += sa_sweep
                    if curr_ra < frs_limit:
                        oa_sweep = min(curr_oa, frs_limit - curr_ra)
                        curr_oa -= oa_sweep; curr_ra += oa_sweep
                    ra_created = True
            else:
                annual_takehome = curr_sal * months_worked
            
            annual_living_costs = (curr_exp + active_mortgage) * 12
            net_cashflow = annual_takehome - annual_living_costs
            
            # 1. Resolve existing debt (e.g., CPF mortgage spillovers) using this year's income
            if curr_cash < 0:
                net_cashflow += curr_cash 
                curr_cash = 0

            # 2. Capital Waterfall for Deficits
            if net_cashflow < 0:
                shortfall = abs(net_cashflow)
                if curr_cash >= shortfall:
                    curr_cash -= shortfall
                else:
                    remaining_debt = shortfall - curr_cash
                    curr_cash = 0
                    curr_invest = max(0, curr_invest - remaining_debt) # Liquidate assets
                annual_surplus = 0
            else:
                annual_surplus = net_cashflow
                
            # 3. Apply safe yield to base cash
            curr_cash *= (1 + cash_yield)
            
            # 4. Allocate Surplus
            if inv_strategy == "Fixed Amount ($)":
                actual_inv = min(monthly_investment * 12, annual_surplus)
                actual_cash = annual_surplus - actual_inv
            elif inv_strategy == "Percentage of Surplus Cash (%)":
                actual_inv = annual_surplus * target_pct
                actual_cash = annual_surplus - actual_inv
            else:
                # Aggressive Sweep
                actual_inv = annual_surplus
                actual_cash = 0     
            
            # Market returns on existing portfolio
            if sim_year in recession_dict:
                shock = recession_dict[sim_year]
                curr_invest = max(0, curr_invest * (1 + shock))
                recovering_from_crash = True  
            else:
                if enable_random_recessions and np.random.random() < recession_prob:
                    random_shock = np.random.uniform(max_drawdown, -0.10)
                    curr_invest = max(0, curr_invest * (1 + random_shock))
                    recovering_from_crash = True  
                else:
                    z = np.random.normal(0, 1)
                    current_mu = expected_market_return
                    if recovering_from_crash:
                        current_mu = expected_market_return * recovery_multiplier
                        recovering_from_crash = False  
                    annual_return = np.exp((current_mu - 0.5 * market_volatility**2) + market_volatility * z) - 1
                    curr_invest = max(0, curr_invest * (1 + annual_return))
            
            # Add this year's surplus to the balances
            curr_cash += actual_cash 
            curr_invest += actual_inv
            
            total_cpf = curr_oa + curr_sa + curr_ma + curr_ra
            all_cash_paths[sim, t] = curr_cash
            all_invest_paths[sim, t] = curr_invest
            all_cpf_paths[sim, t] = total_cpf if include_cpf_in_nw else 0
            all_trajectories[sim, t] = curr_cash + curr_invest + (total_cpf if include_cpf_in_nw else 0)

    p10 = np.percentile(all_trajectories, 10, axis=0)
    p50 = np.percentile(all_trajectories, 50, axis=0)
    p90 = np.percentile(all_trajectories, 90, axis=0)

    # --- 100% CASH ENGINE (DETERMINISTIC BASELINE) ---
    cash_only_trajectory = np.zeros(num_steps)
    cash_only_cash = cash_balance + invested_balance 
    cash_oa, cash_sa, cash_ma, cash_ra = cpf_oa, cpf_sa, cpf_ma, 0
    cash_only_sal, cash_only_exp = current_salary, baseline_monthly_expenses
    cash_only_active_mortgage, cash_only_active_mortgage_cpf = 0, 0
    ra_created_cash = False
    
    cash_only_trajectory[0] = cash_only_cash

    # For reporting purposes
    report_salary = np.zeros(num_steps)
    report_rent = np.zeros(num_steps)
    report_food = np.zeros(num_steps)
    report_util = np.zeros(num_steps)
    report_trans = np.zeros(num_steps)
    report_ent = np.zeros(num_steps)
    report_ins = np.zeros(num_steps)
    report_custom = np.zeros(num_steps)
    report_expenses = np.zeros(num_steps)
    report_mortgage = np.zeros(num_steps)
    report_capex = np.zeros(num_steps)
    
    for t in range(1, num_steps):
        sim_year, sim_age = years_array[t], ages_array[t]
        cash_only_sal *= (1 + salary_growth_array[t])
        cash_only_exp *= (1 + inflation_rate)
        
        if sim_year in capex_dict:
            capex = capex_dict[sim_year] * ((1 + inflation_rate) ** (sim_year - 2026))
            if cash_only_cash >= capex: cash_only_cash -= capex
            else: cash_only_cash = 0
        
        if sim_year in property_events:
            for p_event in property_events[sim_year]:
                inflated_prop = p_event["PropVal"] * ((1 + inflation_rate) ** (sim_year - 2026))
                dp_amount = inflated_prop * (p_event["DP_Pct"] / 100)
                loan_amount = inflated_prop - dp_amount
                
                r, n = (p_event["Rate"] / 100) / 12, p_event["Tenure"] * 12
                monthly_mortgage = loan_amount * (r * (1 + r)**n) / ((1 + r)**n - 1) if r>0 else loan_amount/n
                
                property_failed_cash = False
                if p_event.get("UseCPF", False):
                    if cash_oa + cash_only_cash >= dp_amount:
                        if cash_oa >= dp_amount: cash_oa -= dp_amount
                        else: 
                            cash_only_cash -= (dp_amount - cash_oa)
                            cash_oa = 0
                        cash_only_active_mortgage_cpf += monthly_mortgage
                    else: property_failed_cash = True
                else:
                    if cash_only_cash >= dp_amount:
                        cash_only_cash -= dp_amount
                        cash_only_active_mortgage += monthly_mortgage
                    else: property_failed_cash = True
                
                if not property_failed_cash and p_event.get("AutoReplaceRent", False):
                    cash_only_exp = max(0, cash_only_exp - (rent_mortgage * ((1 + inflation_rate) ** (sim_year - 2026))))

        months_worked = max(0, 12 - jobloss_dict.get(sim_year, 0))
            
        if residency == "Singaporean/PR (CPF)":
            ow_capped = min(cash_only_sal, 8000)
            alloc_oa, alloc_sa, alloc_ma, emp_deduct = get_cpf_allocation(sim_age)
            
            cash_oa += (ow_capped * alloc_oa) * months_worked
            sa_contrib = (ow_capped * alloc_sa) * months_worked
            ma_contrib = (ow_capped * alloc_ma) * months_worked
            annual_takehome = (cash_only_sal - (ow_capped * emp_deduct)) * months_worked
            
            base_bhs_2026 = 71500
            years_to_lock = min(sim_year, 2026 + max(0, 65 - current_age)) - 2026
            current_bhs = base_bhs_2026 * ((1 + inflation_rate) ** years_to_lock)
            
            if cash_ma + ma_contrib > current_bhs:
                overflow = (cash_ma + ma_contrib) - current_bhs
                cash_ma = current_bhs
                if sim_age < 55: cash_sa += sa_contrib + overflow
                else: cash_sa += sa_contrib; cash_ra += overflow
            else:
                cash_ma += ma_contrib; cash_sa += sa_contrib
                
            annual_cpf_mort = cash_only_active_mortgage_cpf * 12
            if cash_oa >= annual_cpf_mort: cash_oa -= annual_cpf_mort
            else: cash_only_cash -= (annual_cpf_mort - cash_oa); cash_oa = 0
                
            cash_oa *= 1.025; cash_sa *= 1.04; cash_ma *= 1.04; cash_ra *= 1.04
            
            combined_bal = cash_oa + cash_sa + cash_ma + cash_ra
            bonus_base = min(combined_bal, 60000)
            smr_base = min(cash_sa + cash_ma + cash_ra, bonus_base)
            oa_base = min(cash_oa, 20000, bonus_base - smr_base)
            
            cash_oa += oa_base * 0.01
            if smr_base > 0:
                cash_sa += (cash_sa / (cash_sa + cash_ma + cash_ra)) * smr_base * 0.01
                cash_ma += (cash_ma / (cash_sa + cash_ma + cash_ra)) * smr_base * 0.01
                cash_ra += (cash_ra / (cash_sa + cash_ma + cash_ra)) * smr_base * 0.01
            
            if sim_age >= 55 and not ra_created_cash:
                frs_limit = 220000 * ((1 + inflation_rate) ** t)
                sa_sweep = min(cash_sa, frs_limit)
                cash_sa -= sa_sweep; cash_ra += sa_sweep
                if cash_ra < frs_limit:
                    oa_sweep = min(cash_oa, frs_limit - cash_ra)
                    cash_oa -= oa_sweep; cash_ra += oa_sweep
                ra_created_cash = True
        else:
            annual_takehome = cash_only_sal * months_worked
        
        annual_living_costs = (cash_only_exp + cash_only_active_mortgage) * 12
        net_cashflow = annual_takehome - annual_living_costs
        
        # 1. Resolve existing debt
        if cash_only_cash < 0:
            net_cashflow += cash_only_cash
            cash_only_cash = 0
            
        # 2. Deficit Waterfall & Yield Application
        if net_cashflow < 0:
            shortfall = abs(net_cashflow)
            cash_only_cash -= shortfall 
            cash_only_cash *= (1 + cash_yield) 
        else:
            cash_only_cash *= (1 + cash_yield)
            cash_only_cash += net_cashflow
        
        cash_cpf_total = cash_oa + cash_sa + cash_ma + cash_ra
        cash_only_trajectory[t] = cash_only_cash
        
        # For reporting purpose
        inf_mult = (1 + inflation_rate) ** t
        is_rent_replaced = any(p.get("AutoReplaceRent", False) for y, p_list in property_events.items() if y <= sim_year for p in p_list)
        
        base_rent = 0 if is_rent_replaced else (rent_mortgage * 12 * inf_mult)
        total_mortgage = (cash_only_active_mortgage + cash_only_active_mortgage_cpf) * 12
        cash_mortgage = cash_only_active_mortgage * 12
        
        report_salary[t] = annual_takehome
        report_rent[t] = base_rent + total_mortgage 
        
        report_food[t] = food_bev * 12 * inf_mult
        report_util[t] = utilities_bills * 12 * inf_mult
        report_trans[t] = transport_travel * 12 * inf_mult
        report_ent[t] = entertainment * 12 * inf_mult
        report_ins[t] = other_exp * 12 * inf_mult
        report_custom[t] = custom_spending_total * 12 * inf_mult
        
        # Total Cash Living Expenses strictly tracks cash outlays (excludes the CPF mortgage portion)
        report_expenses[t] = base_rent + cash_mortgage + report_food[t] + report_util[t] + report_trans[t] + report_ent[t] + report_ins[t] + report_custom[t]
        
        report_capex[t] = capex if sim_year in capex_dict else 0

    if "Today's Value" in display_mode:
        discount_factors = (1 + inflation_rate) ** np.arange(num_steps)
        p10 /= discount_factors
        p50 /= discount_factors
        p90 /= discount_factors
        cash_only_trajectory /= discount_factors
        
        p50_cash = np.percentile(all_cash_paths, 50, axis=0) / discount_factors
        p50_invest = np.percentile(all_invest_paths, 50, axis=0) / discount_factors
        p50_cpf = np.percentile(all_cpf_paths, 50, axis=0) / discount_factors
    else:
        p50_cash = np.percentile(all_cash_paths, 50, axis=0)
        p50_invest = np.percentile(all_invest_paths, 50, axis=0)
        p50_cpf = np.percentile(all_cpf_paths, 50, axis=0)
        
    df_results = pd.DataFrame({
        "Age": ages_array, "Worst-Case": p10, "Median Trajectory": p50,
        "Best-Case": p90, "Target Net Worth": [target_nw] * num_steps, "100% Cash": cash_only_trajectory
    }).round(0)

    st.subheader("Simulated Wealth Trajectory")
    
    df_lines = df_results[["Age", "Median Trajectory", "Target Net Worth"]].melt("Age", var_name="Scenario", value_name="Net Worth")
    color_scale = alt.Scale(domain=["Median Trajectory","Target Net Worth"], range=["#3182bd", "#e74c3c"])
    
    lines = alt.Chart(df_lines).mark_line().encode(
        x=alt.X("Age:Q", axis=alt.Axis(tickMinStep=1, format="d")),
        y=alt.Y("Net Worth:Q", axis=alt.Axis(format="$,.0f")),
        color=alt.Color("Scenario:N", scale=color_scale, legend=alt.Legend(title="Trajectory", orient="bottom")),
        strokeDash=alt.condition(alt.datum.Scenario == "Target Net Worth", alt.value([5, 5]), alt.value([0])),
        size=alt.condition(alt.datum.Scenario == "Median Trajectory", alt.value(3), alt.value(2)),
        tooltip=[alt.Tooltip("Age:Q", title="Age"), alt.Tooltip("Scenario:N", title="Scenario"), alt.Tooltip("Net Worth:Q", format="$,.0f", title="Net Worth")]
    )
    
    area = alt.Chart(df_results).mark_area(opacity=0.2, color="#3182bd").encode(
        x=alt.X("Age:Q"), y=alt.Y("Worst-Case:Q", title="Net Worth ($)"), y2="Best-Case:Q",
        tooltip=[alt.Tooltip("Age:Q", title="Age"), alt.Tooltip("Best-Case:Q", format="$,.0f", title="Best-Case (90th %ile)"), alt.Tooltip("Worst-Case:Q", format="$,.0f", title="Worst-Case (10th %ile)")]
    )
    
    chart = (area + lines).interactive()
    st.altair_chart(chart, width='stretch')
    
    st.subheader("Median Wealth Composition (Liquidity Breakdown)")
    st.caption("This chart breaks down your median trajectory so you can identify if your wealth is tied up in illiquid assets.")

    df_comp = pd.DataFrame({
        "Age": ages_array,
        "Uninvested Cash": p50_cash,
        "Invested Portfolio": p50_invest,
        "Locked CPF": p50_cpf
    }).round(0)

    df_comp_melted = df_comp.melt("Age", var_name="Asset Class", value_name="Value")

    comp_chart = alt.Chart(df_comp_melted).mark_area().encode(
        x=alt.X("Age:Q", axis=alt.Axis(tickMinStep=1, format="d")),
        y=alt.Y("Value:Q", axis=alt.Axis(format="$,.0f"), title="Median Net Worth ($)"),
        color=alt.Color("Asset Class:N",
                        scale=alt.Scale(
                            domain=["Locked CPF", "Invested Portfolio", "Uninvested Cash"], 
                            range=["#bdc3c7", "#3182bd", "#2ecc71"] 
                        ),
                        legend=alt.Legend(title="Asset Type", orient="bottom")),
        tooltip=[
            alt.Tooltip("Age:Q", title="Age"),
            alt.Tooltip("Asset Class:N", title="Category"),
            alt.Tooltip("Value:Q", format="$,.0f", title="Amount")
        ]
    ).interactive()

    st.altair_chart(comp_chart, width='stretch')
    
    final_median = p50[-1]
    final_p10 = p10[-1]
    final_cash = cash_only_trajectory[-1]
    final_cpf = p50_cpf[-1]
    prob_success = np.mean(all_trajectories[:, -1] >= target_nw) * 100

    # Dynamically display 5 columns if CPF is included, or 4 if it is excluded
    if include_cpf_in_nw:
        col_res1, col_res2, col_res3, col_res4, col_res5 = st.columns(5)
        col_res1.metric("Probability of Goal", f"{prob_success:.1f}%")
        col_res2.metric(f"Median Expected Total", f"${final_median:,.0f}")
        col_res3.metric("Downside Stress Floor", f"${final_p10:,.0f}")
        col_res4.metric("Hypothetical: 100% Bank", f"${final_cash:,.0f}", help="Your final wealth if you kept all surplus in the bank at the cash yield rate, strictly excluding CPF and market returns.")
        col_res5.metric("Amount Locked in CPF", f"${final_cpf:,.0f}")
    else:
        col_res1, col_res2, col_res3, col_res4 = st.columns(4)
        col_res1.metric("Probability of Goal", f"{prob_success:.1f}%")
        col_res2.metric(f"Median Expected Total", f"${final_median:,.0f}")
        col_res3.metric("Downside Stress Floor", f"${final_p10:,.0f}")
        col_res4.metric("Hypothetical: 100% Bank", f"${final_cash:,.0f}", help="Your final wealth if you kept all surplus in the bank at the cash yield rate, strictly excluding CPF and market returns.")

    if prob_success >= 75: 
        st.success(f"✅ High confidence path: {prob_success:.1f}% chance of exceeding ${target_nw:,.0f} by age {target_age}.")
    elif prob_success >= 50: 
        st.warning(f"⚠️ Moderate confidence ({prob_success:.1f}%). Consider increasing monthly savings or reducing milestone drag.")
    else: 
        st.error(f"🚨 High shortfall risk. Adjust timeline, lower milestone outflows, or modify asset return targets.")

    st.divider()
    st.subheader(f"📊 Terminal Wealth Distribution at Age {target_age}")
    st.caption("Notice the log-normal, right-tail skew: the median outcome is highly localized, but extreme bull markets stretch the upside.")
    
    final_nw_raw = all_trajectories[:, -1] / (((1 + inflation_rate) ** (num_steps - 1)) if "Today's Value" in display_mode else 1)
    hist_counts, bin_edges = np.histogram(final_nw_raw, bins=40)
    bin_mids = (bin_edges[:-1] + bin_edges[1:]) / 2
    
    df_hist = pd.DataFrame({"Frequency": hist_counts, "Net Worth Bracket": [f"${x/1000000:.1f}M" for x in bin_mids]}).set_index("Net Worth Bracket")
    st.bar_chart(df_hist)
    
    df_report = pd.DataFrame({
        "Year": years_array,
        "Age": ages_array,
        "Take-Home Salary": report_salary,
        "Housing (Rent/Mortgage)": report_rent,
        "Food & Dining": report_food,
        "Utilities & Telco": report_util,
        "Transport": report_trans,
        "Leisure": report_ent,
        "Insurance": report_ins,
        "Custom Spends": report_custom,
        "Total Cash Living Expenses": report_expenses,
        "Major Events (Capex)": report_capex,
        "Median Invested Net Worth": p50,
        "Total CPF Balance": p50_cpf,
        "100% Cash Baseline": cash_only_trajectory
    }).round(0)
    
    # --- SAVE TO SESSION STATE SO IT SURVIVES BUTTON CLICKS ---
    st.session_state.df_report = df_report
    st.session_state.prob_success = prob_success
    st.session_state.initial_total_nw = initial_total_nw
    st.session_state.final_median = final_median
    st.session_state.final_cash = final_cash
    st.session_state.final_cpf = final_cpf
    st.session_state.simulation_run = True

# ==========================================
# 5. POST-SIMULATION: REPORTING & AI
# ==========================================
# This guardrail ensures the UI only loads AFTER a successful simulation
if st.session_state.simulation_run:
    st.divider()
    
    # Show the table in a clean, collapsible expander
    with st.expander("🔍 View Itemized Year-by-Year Financial Ledger", expanded=False):
        st.dataframe(st.session_state.df_report, width='stretch')
    
    # --- AI EXECUTIVE SUMMARY & EXCEL EXPORT ---
    st.subheader("🤖 Generate Smart Excel Report")
    st.caption("The AI will analyze your specific expenses and bundle a custom narrative directly into an Excel file alongside your ledger.")
    
    # Setup an expander for API Key input so you don't hardcode it
    api_key = st.text_input("Enter Gemini API Key:", type="password")
    
    if st.button("Generate Excel Report") and api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-flash-latest')
        
        # Structure the prompt with the exact simulation results AND specific expenses
        prompt = f"""
        You are an expert, empathetic financial advisor. Summarize the following Monte Carlo financial simulation for a {target_age - current_age}-year forecast.
        
        Data Points:
        - Target Net Worth: ${target_nw:,.0f}
        - Probability of Success: {st.session_state.prob_success:.1f}%
        - Starting Net Worth: ${st.session_state.initial_total_nw:,.0f}
        - Projected Median Net Worth at Age {target_age}: ${st.session_state.final_median:,.0f}
        
        Monthly Expense Breakdown:
        - Rent/Mortgage: ${rent_mortgage}
        - Food & Dining: ${food_bev}
        - Utilities: ${utilities_bills}
        - Transport: ${transport_travel}
        - Leisure: ${entertainment}
        - Insurance: ${other_exp}
        - Custom/Other: ${custom_spending_total}
        
        Write a 3-paragraph executive summary. 
        Paragraph 1: Assess their probability of success and overall trajectory.
        Paragraph 2: Analyze their monthly expenses. Identify 1 or 2 specific categories from the list above where they could reasonably cut back to increase their investment allocation and accelerate compounding.
        Paragraph 3: Give a concrete piece of advice comparing their invested trajectory versus the baseline of holding 100% cash.
        Do not use generic disclaimers. Tone should be professional, candid, and highly actionable.
        """
        
        with st.spinner("Analyzing expenses and building Excel report..."):
            try:
                # 1. Get the AI Response
                response = model.generate_content(prompt)
                ai_text = response.text
                
                # 2. Build the Excel File in Memory
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    # Write the DataFrame to Sheet 1
                    st.session_state.df_report.to_excel(writer, sheet_name='Financial Ledger', index=False)
                    
                    # Write the AI Narrative to Sheet 2
                    workbook = writer.book
                    summary_sheet = workbook.add_worksheet('AI Executive Summary')
                    
                    format_wrap = workbook.add_format({'text_wrap': True, 'valign': 'top'})
                    format_bold = workbook.add_format({'bold': True, 'font_size': 14})
                    
                    summary_sheet.set_column('A:A', 120) # Make the column wide enough to read like a document
                    summary_sheet.write('A1', "AI Executive Summary", format_bold)
                    summary_sheet.write('A3', ai_text, format_wrap)
                    
                # Save to session state
                st.session_state.excel_data = output.getvalue()
                
            except Exception as e:
                st.error(f"Failed to generate report: {e}")
                
    # If the Excel file exists in memory, show the download button
    if "excel_data" in st.session_state:
        st.success("✅ Report generated successfully!")
        st.download_button(
            label="📥 Download Complete Report (Excel)",
            data=st.session_state.excel_data,
            file_name="wealth_forecast_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
