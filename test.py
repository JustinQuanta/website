import streamlit as st
import numpy as np
import pandas as pd
from collections import defaultdict

st.set_page_config(page_title="Net Worth Monte Carlo Forecaster", layout="wide")

st.title("📈 Advanced Net Worth & Goal Forecaster")
st.caption("Institutional-grade wealth trajectory simulation with macroeconomic shocks & cashflow constraints.")

# --- Initialize Session States ---
if "events" not in st.session_state:
    st.session_state.events = []
if "custom_spending" not in st.session_state:
    st.session_state.custom_spending = []

# ==========================================
# 1. SIDEBAR: Profile & Asset Starting Point
# ==========================================
st.sidebar.header("1. Core Profile & Baseline")
col_sb1, col_sb2 = st.sidebar.columns(2)
with col_sb1:
    current_age = st.number_input("Current Age", min_value=18, max_value=80, value=30)
    current_salary = st.number_input("Monthly Gross Salary ($)", min_value=0, value=6000, step=250)
    salary_growth = st.number_input("Salary Growth (% p.a.)", min_value=0.0, max_value=20.0, value=3.5, step=0.5) / 100
with col_sb2:
    target_age = st.number_input("Target Age", min_value=current_age + 1, max_value=100, value=45)
    target_nw = st.number_input("Net Worth Target ($)", min_value=0, value=1_000_000, step=50_000)
    inflation_rate = st.number_input("Inflation (% p.a.)", min_value=0.0, max_value=15.0, value=2.5, step=0.25) / 100

residency = st.sidebar.selectbox("Residency / Pension Status", ["Singaporean/PR (CPF)", "WP/EP/SP (No CPF)"])

include_cpf_in_nw = False
if residency == "Singaporean/PR (CPF)":
    include_cpf_in_nw = st.sidebar.checkbox("Include CPF in Target Net Worth", value=True)

st.sidebar.header("2. Starting Asset Balances")
cash_balance = st.sidebar.number_input("Liquid Cash / Emergency Fund ($)", min_value=0, value=25000, step=5000)
invested_balance = st.sidebar.number_input("Invested Portfolio ($)", min_value=0, value=60000, step=5000)

cpf_balance = 0
if residency == "Singaporean/PR (CPF)" and include_cpf_in_nw:
    cpf_balance = st.sidebar.number_input("Current CPF Balance ($)", min_value=0, value=35000, step=5000)

# ==========================================
# 2. BUDGET INGESTION & CASHFLOW
# ==========================================
st.header("1. Cash Flow & Budget Configuration")

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
    
    # --- UPGRADE 1: Standardized Custom Spending UI ---
    st.markdown("##### Custom Discretionary Spending")
    
    with st.expander("➕ Add Custom Spending Category", expanded=False):
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
                
        st.write("") # Spacer
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
                inflows = df_budget[df_budget['Cashflow_Type'].str.strip().str.lower() == 'inflow']['Amount'].sum()
                outflows = df_budget[df_budget['Cashflow_Type'].str.strip().str.lower() == 'outflow']['Amount'].sum()
                st.success(f"File verified! Detected monthly outflows: ${outflows:,.2f}")
                baseline_monthly_expenses = outflows
            else:
                st.error(f"Missing required columns. Schema must contain: {req_cols}")
        except Exception as e:
            st.error(f"Error reading file: {e}")

st.metric("Total Monthly Baseline Expenses", f"${baseline_monthly_expenses:,.2f}")

# ==========================================
# 3. MILESTONES & STRESS-TEST EVENTS
# ==========================================
st.header("2. Major Life Events & Financial Bumps")
st.caption("💡 **Tip:** If adding a Property Downpayment, remember that your post-purchase 'Rent' will likely convert to a 'Mortgage'. Ensure your baseline budget reflects this blended average.")

# --- UPGRADE 2: Standardized Event UI ---
with st.expander("➕ Add an Event (House, Wedding, Job Loss, Crash)", expanded=False):
    col_e1, col_e2, col_e3, col_e4 = st.columns([1, 2, 2, 1])
    with col_e1:
        ev_year = st.number_input("Event Year", min_value=2026, max_value=2065, value=2028)
    with col_e2:
        ev_type = st.selectbox("Event Category", [
            "Market Recession (Drawdown)", 
            "Property Downpayment / Capex", 
            "Job Loss / Income Sabbatical"
        ])
    with col_e3:
        if ev_type == "Market Recession (Drawdown)":
            ev_val = st.slider("Equity Drawdown (%)", min_value=-60, max_value=-5, value=-25, step=5)
        elif ev_type == "Property Downpayment / Capex":
            ev_val = st.number_input("One-off Outflow (Today's $)", min_value=0, value=80000, step=5000)
        else:
            ev_val = st.slider("Duration without Income (Months)", min_value=1, max_value=12, value=4)
    with col_e4:
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
        
        if event["Type"] == "Market Recession (Drawdown)":
            val_display = f"{event['Magnitude']}% Drop"
        elif event["Type"] == "Property Downpayment / Capex":
            val_display = f"${event['Magnitude']:,}"
        else:
            val_display = f"{event['Magnitude']} Months"
            
        col_ev1.write(f"**{event['Year']}** | {event['Type']} ➔ {val_display}")
        
        if col_ev2.button("❌ Remove", key=f"del_event_{i}"):
            st.session_state.events.pop(i)
            st.rerun()
            
    st.write("") # Spacer
    if st.button("🗑️ Clear All Events"):
        st.session_state.events = []
        st.rerun()

# ==========================================
# 4. SIMULATION ENGINE (Monte Carlo + Baseline)
# ==========================================
st.header("3. Run Wealth Projection Engine")

col_m1, col_m2 = st.columns(2)
with col_m1:
    expected_market_return = st.number_input("Expected Equity Return (% p.a.)", value=7.5, step=0.5) / 100
with col_m2:
    market_volatility = st.number_input("Market Volatility ($\sigma$ % p.a.)", min_value=0.0, value=16.0, step=1.0) / 100

st.subheader("Stochastic Recession Settings")
col_r1, col_r2, col_r3, col_r4 = st.columns(4)
with col_r1:
    enable_random_recessions = st.checkbox("Enable Random Market Crashes", value=True)
with col_r2:
    recession_prob = st.slider("Annual Crash Probability (%)", min_value=1, max_value=25, value=10) / 100
with col_r3:
    max_drawdown = st.slider("Maximum Drawdown Limit (%)", min_value=-80, max_value=-15, value=-40) / 100
with col_r4:
    recovery_multiplier = st.slider("Post-Crash Recovery Multiplier", min_value=1.0, max_value=3.0, value=1.5, step=0.1)

with st.expander("⚙️ Advanced Engine Settings (Tune Iterations)"):
    sim_iterations = st.number_input("Monte Carlo Iterations", min_value=1000, max_value=500000, value=100000, step=10000)
    st.caption("Note: Running 100,000+ paths provides extreme statistical accuracy but may take a few seconds to process.")

if st.button("🚀 Execute Monte Carlo Simulation", type="primary"):
    
    with st.spinner(f"Computing {sim_iterations:,} alternate timelines..."):
        
        horizon_years = target_age - current_age
        years_array = np.arange(2026, 2026 + horizon_years + 1)
        ages_array = np.arange(current_age, target_age + 1)
        num_steps = len(years_array)
    
    capex_dict = defaultdict(float)
    recession_dict = {}
    jobloss_dict = defaultdict(int)
    
    for e in st.session_state.events:
        if e["Type"] == "Property Downpayment / Capex":
            capex_dict[e["Year"]] += e["Magnitude"]
        elif e["Type"] == "Market Recession (Drawdown)":
            recession_dict[e["Year"]] = e["Magnitude"] / 100
        elif e["Type"] == "Job Loss / Income Sabbatical":
            jobloss_dict[e["Year"]] += e["Magnitude"]

    all_trajectories = np.zeros((sim_iterations, num_steps))
    
    initial_total_nw = cash_balance + invested_balance + (cpf_balance if include_cpf_in_nw else 0)
    all_trajectories[:, 0] = initial_total_nw
    
    for sim in range(sim_iterations):
        curr_cash = cash_balance
        curr_invest = invested_balance
        curr_cpf = cpf_balance
        curr_sal = current_salary
        curr_exp = baseline_monthly_expenses

        recovering_from_crash = False

        for t in range(1, num_steps):
            sim_year = years_array[t]
            
            curr_sal *= (1 + salary_growth)
            curr_exp *= (1 + inflation_rate)
            
            months_worked = max(0, 12 - jobloss_dict.get(sim_year, 0))
                
            if residency == "Singaporean/PR (CPF)":
                ow_subject_to_cpf = min(curr_sal, 8000)
                employee_cpf_deduction = ow_subject_to_cpf * 0.20
                total_annual_cpf_contrib = (ow_subject_to_cpf * 0.37) * months_worked
                curr_cpf = (curr_cpf * 1.03) + total_annual_cpf_contrib 
                annual_takehome = (curr_sal - employee_cpf_deduction) * months_worked
            else:
                annual_takehome = curr_sal * months_worked
                curr_cpf = 0
            
            annual_living_costs = curr_exp * 12
            annual_savings = max(0, annual_takehome - annual_living_costs)
            
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
            
            curr_cash *= 1.015 
            curr_invest += annual_savings
            
            if sim_year in capex_dict:
                years_from_start = sim_year - 2026
                inflated_capex = capex_dict[sim_year] * ((1 + inflation_rate) ** years_from_start)
                
                if curr_cash >= inflated_capex:
                    curr_cash -= inflated_capex
                else:
                    deficit = inflated_capex - curr_cash
                    curr_cash = 0
                    curr_invest = max(0, curr_invest - deficit)
            
            step_nw = curr_cash + curr_invest + (curr_cpf if include_cpf_in_nw else 0)
            all_trajectories[sim, t] = step_nw

    p10 = np.percentile(all_trajectories, 10, axis=0)
    p50 = np.percentile(all_trajectories, 50, axis=0)
    p90 = np.percentile(all_trajectories, 90, axis=0)
    
    df_results = pd.DataFrame({
        "Age": ages_array,
        "Worst-Case": p10,
        "Median Trajectory": p50,
        "Best-Case": p90,
        "Targeted Net Worth": [target_nw] * num_steps
    }).set_index("Age")

    st.subheader("Simulated Wealth Trajectory")
    st.line_chart(df_results)

    final_median = p50[-1]
    final_p10 = p10[-1]
    prob_success = np.mean(all_trajectories[:, -1] >= target_nw) * 100

    col_res1, col_res2, col_res3 = st.columns(3)
    col_res1.metric("Probability of Reaching Goal", f"{prob_success:.1f}%")
    col_res2.metric(f"Median Outcome (Age {target_age})", f"${final_median:,.0f}")
    col_res3.metric("Downside Stress Floor (10th %ile)", f"${final_p10:,.0f}")

    if prob_success >= 75:
        st.success(f"✅ High confidence path: {prob_success:.1f}% chance of exceeding ${target_nw:,.0f} by age {target_age}.")
    elif prob_success >= 50:
        st.warning(f"⚠️ Moderate confidence ({prob_success:.1f}%). Consider increasing monthly savings or reducing milestone drag.")
    else:
        st.error(f"🚨 High shortfall risk. Adjust timeline, lower milestone outflows, or modify asset return targets.")

    st.divider()
    st.subheader(f"📊 Terminal Wealth Distribution at Age {target_age}")
    st.caption("Notice the log-normal, right-tail skew: the median outcome is highly localized, but extreme bull markets stretch the upside.")
    
    final_net_worths = all_trajectories[:, -1]
    hist_counts, bin_edges = np.histogram(final_net_worths, bins=40)
    bin_mids = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_labels = [f"${x/1000000:.1f}M" for x in bin_mids]
    
    df_hist = pd.DataFrame({
        "Frequency": hist_counts,
        "Net Worth Bracket": bin_labels
    }).set_index("Net Worth Bracket")
    
    st.bar_chart(df_hist)