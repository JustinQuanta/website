import streamlit as st
import numpy as np
import pandas as pd
import altair as alt
from collections import defaultdict

st.set_page_config(page_title="Wealth & Retirement Forecaster", layout="wide")

st.title("📈 Wealth & Retirement Forecaster")
st.caption("Plan your financial future by simulating life events, market crashes, and property purchases.")

# --- Initialize Session States ---
if "events" not in st.session_state:
    st.session_state.events = []
if "custom_spending" not in st.session_state:
    st.session_state.custom_spending = []

# ==========================================
# 1. SIDEBAR: Profile & Asset Starting Point
# ==========================================
st.sidebar.header("1. Your Basic Profile (Start Here)")
col_sb1, col_sb2 = st.sidebar.columns(2)
with col_sb1:
    current_age = st.number_input("Current Age", min_value=18, max_value=80, value=30)
    current_salary = st.number_input("Monthly Gross Salary ($)", min_value=0, value=6000, step=250)
    salary_growth = st.number_input("Salary Growth (% p.a.)", min_value=0.0, max_value=20.0, value=3.5, step=0.5) / 100
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

cpf_balance = 0
if residency == "Singaporean/PR (CPF)" and include_cpf_in_nw:
    cpf_balance = st.sidebar.number_input("Current CPF Balance ($)", min_value=0, value=0, step=5000)

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
    
    st.markdown("##### Monthly Savings & Investing Plan")
    monthly_investment = st.number_input("Amount to Invest in Stocks Monthly ($)", min_value=0, value=1000, step=100, help="This is the portion of your leftover cash you want to put into the market. Anything else will go straight to your bank/cash savings.")

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
                monthly_investment = st.number_input("Amount to Invest in Stocks Monthly ($)", min_value=0, value=1000, step=100)
            else:
                st.error(f"Missing required columns. Schema must contain: {req_cols}")
        except Exception as e:
            st.error(f"Error reading file: {e}")

# --- UPGRADE: Dynamic Take-Home Pay & Budget Validation ---
if residency == "Singaporean/PR (CPF)":
    ow_subject_to_cpf = min(current_salary, 8000)
    employee_cpf_deduction = ow_subject_to_cpf * 0.20
    take_home_pay = current_salary - employee_cpf_deduction
else:
    take_home_pay = current_salary

total_outflow = baseline_monthly_expenses + monthly_investment
surplus_cash = take_home_pay - total_outflow

st.write("---")
col_met1, col_met2, col_met3 = st.columns(3)
col_met1.metric("Take-Home Pay (After CPF)", f"${take_home_pay:,.2f}")
col_met2.metric("Total Baseline Expenses", f"${baseline_monthly_expenses:,.2f}")
col_met3.metric("Target Monthly Invest", f"${monthly_investment:,.2f}")

valid_budget = True
if surplus_cash < 0:
    st.error(f"🚨 **Budget Deficit Detected!** Your expenses and investments (${total_outflow:,.2f}) exceed your take-home pay by **\${abs(surplus_cash):,.2f}** a month. Please reduce your expenses or investment amount. The simulation cannot run with a negative cash flow.")
    valid_budget = False
elif surplus_cash > 0:
    st.info(f"💡 **Uninvested Cash:** You have **${surplus_cash:,.2f}** left over every month. This money will automatically be swept into your liquid Bank/Emergency fund (growing at your safe cash yield rate) instead of the stock market.")

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
            auto_replace_rent = st.checkbox("Stop paying baseline rent after purchase?", value=True, help="Automatically subtracts your baseline Rent from your expenses once this mortgage starts.")
            st.write("")
            if st.button("Add Property Event"):
                st.session_state.events.append({
                    "Year": ev_year,
                    "Type": ev_type,
                    "PropVal": prop_val,
                    "DP_Pct": dp_pct,
                    "Rate": mortgage_rate,
                    "Tenure": tenure,
                    "AutoReplaceRent": auto_replace_rent
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
    sim_iterations = st.number_input("Monte Carlo Iterations", min_value=10000, max_value=500000, value=100000, step=10000)
    st.caption("Note: Running 100,000+ paths provides extreme statistical accuracy but may take a few seconds to process.")

st.subheader("How do you want to view your results?")
display_mode = st.radio("Display Projection In:", ["Future Value (Without factoring in inflation)", "Today's Value (Adjusted for inflation)"], horizontal=True)

# Disable the execute button if the budget constraint fails
if st.button("🚀 Execute Simulation", type="primary", disabled=not valid_budget):
    
    with st.spinner(f"Computing {sim_iterations:,} alternate timelines..."):
        
        horizon_years = target_age - current_age
        years_array = np.arange(2026, 2026 + horizon_years + 1)
        ages_array = np.arange(current_age, target_age + 1)
        num_steps = len(years_array)
    
    capex_dict = defaultdict(float)
    recession_dict = {}
    jobloss_dict = defaultdict(int)
    property_events = defaultdict(list)
    
    # Map Events
    for e in st.session_state.events:
        if e["Type"] == "Large One-Off Purchase (Wedding, Car, etc.)":
            capex_dict[e["Year"]] += e["Magnitude"]
        elif e["Type"] == "Market Crash (Stock Market Drop)":
            recession_dict[e["Year"]] = e["Magnitude"] / 100
        elif e["Type"] == "Job Loss / Career Break":
            jobloss_dict[e["Year"]] += e["Magnitude"]
        elif e["Type"] == "Property Purchase":
            property_events[e["Year"]].append(e)

    # --- THE MONTE CARLO ENGINE (INVESTMENT FOCUSED) ---
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
        active_mortgage = 0 

        for t in range(1, num_steps):
            sim_year = years_array[t]
            
            curr_sal *= (1 + salary_growth)
            curr_exp *= (1 + inflation_rate)
            
            if sim_year in property_events:
                for p_event in property_events[sim_year]:
                    years_from_start = sim_year - 2026
                    inflated_prop_val = p_event["PropVal"] * ((1 + inflation_rate) ** years_from_start)
                    dp_amount = inflated_prop_val * (p_event["DP_Pct"] / 100)
                    loan_amount = inflated_prop_val - dp_amount
                    
                    if curr_cash >= dp_amount:
                        curr_cash -= dp_amount
                    else:
                        deficit = dp_amount - curr_cash
                        curr_cash = 0
                        curr_invest = max(0, curr_invest - deficit)
                    
                    r = (p_event["Rate"] / 100) / 12
                    n = p_event["Tenure"] * 12
                    if r > 0 and n > 0:
                        monthly_mortgage = loan_amount * (r * (1 + r)**n) / ((1 + r)**n - 1)
                    else:
                        monthly_mortgage = loan_amount / n if n > 0 else 0
                        
                    active_mortgage += monthly_mortgage
                    
                    if p_event.get("AutoReplaceRent", False):
                        inflated_rent = rent_mortgage * ((1 + inflation_rate) ** years_from_start)
                        curr_exp = max(0, curr_exp - inflated_rent)

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
            
            annual_living_costs = (curr_exp + active_mortgage) * 12
            annual_surplus = max(0, annual_takehome - annual_living_costs)
            
            planned_investment = monthly_investment * 12
            actual_investment = min(planned_investment, annual_surplus)
            actual_cash_savings = annual_surplus - actual_investment
            
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
            
            curr_cash = (curr_cash * (1 + cash_yield)) + actual_cash_savings 
            curr_invest += actual_investment
            
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

    # Calculate Percentiles
    p10 = np.percentile(all_trajectories, 10, axis=0)
    p50 = np.percentile(all_trajectories, 50, axis=0)
    p90 = np.percentile(all_trajectories, 90, axis=0)

    # --- THE 100% CASH ENGINE (DETERMINISTIC BASELINE) ---
    cash_only_trajectory = np.zeros(num_steps)
    cash_only_cash = cash_balance + invested_balance 
    cash_only_cpf = cpf_balance
    cash_only_sal = current_salary
    cash_only_exp = baseline_monthly_expenses
    cash_only_active_mortgage = 0
    cash_only_trajectory[0] = cash_only_cash + (cash_only_cpf if include_cpf_in_nw else 0)

    for t in range(1, num_steps):
        sim_year = years_array[t]
        
        cash_only_sal *= (1 + salary_growth)
        cash_only_exp *= (1 + inflation_rate)
        
        if sim_year in property_events:
            for p_event in property_events[sim_year]:
                years_from_start = sim_year - 2026
                inflated_prop_val = p_event["PropVal"] * ((1 + inflation_rate) ** years_from_start)
                dp_amount = inflated_prop_val * (p_event["DP_Pct"] / 100)
                loan_amount = inflated_prop_val - dp_amount
                
                if cash_only_cash >= dp_amount:
                    cash_only_cash -= dp_amount
                else:
                    cash_only_cash = 0
                
                r = (p_event["Rate"] / 100) / 12
                n = p_event["Tenure"] * 12
                if r > 0 and n > 0:
                    monthly_mortgage = loan_amount * (r * (1 + r)**n) / ((1 + r)**n - 1)
                else:
                    monthly_mortgage = loan_amount / n if n > 0 else 0
                    
                cash_only_active_mortgage += monthly_mortgage
                
                if p_event.get("AutoReplaceRent", False):
                    inflated_rent = rent_mortgage * ((1 + inflation_rate) ** years_from_start)
                    cash_only_exp = max(0, cash_only_exp - inflated_rent)

        months_worked = max(0, 12 - jobloss_dict.get(sim_year, 0))
            
        if residency == "Singaporean/PR (CPF)":
            ow_subject_to_cpf = min(cash_only_sal, 8000)
            employee_cpf_deduction = ow_subject_to_cpf * 0.20
            total_annual_cpf_contrib = (ow_subject_to_cpf * 0.37) * months_worked
            cash_only_cpf = (cash_only_cpf * 1.03) + total_annual_cpf_contrib 
            annual_takehome = (cash_only_sal - employee_cpf_deduction) * months_worked
        else:
            annual_takehome = cash_only_sal * months_worked
            cash_only_cpf = 0
        
        annual_living_costs = (cash_only_exp + cash_only_active_mortgage) * 12
        annual_surplus = max(0, annual_takehome - annual_living_costs)
        
        cash_only_cash = (cash_only_cash * (1 + cash_yield)) + annual_surplus 
        
        if sim_year in capex_dict:
            years_from_start = sim_year - 2026
            inflated_capex = capex_dict[sim_year] * ((1 + inflation_rate) ** years_from_start)
            
            if cash_only_cash >= inflated_capex:
                cash_only_cash -= inflated_capex
            else:
                cash_only_cash = 0
        
        cash_only_trajectory[t] = cash_only_cash + (cash_only_cpf if include_cpf_in_nw else 0)

    # --- APPLY INFLATION DISCOUNT IF "REAL DOLLARS" SELECTED ---
    if "Today's Value" in display_mode:
        discount_factors = (1 + inflation_rate) ** np.arange(num_steps)
        p10 = p10 / discount_factors
        p50 = p50 / discount_factors
        p90 = p90 / discount_factors
        cash_only_trajectory = cash_only_trajectory / discount_factors

    df_results = pd.DataFrame({
        "Age": ages_array,
        "Worst-Case": p10,
        "Median Trajectory": p50,
        "Best-Case": p90,
        "Target Net Worth": [target_nw] * num_steps,
        "100% Cash": cash_only_trajectory
    }).round(0)

    st.subheader("Simulated Wealth Trajectory")
    
    # --- ALTAIR SHADED FAN CHART WITH LEGEND ---
    df_lines = df_results[["Age", "Median Trajectory", "100% Cash", "Target Net Worth"]].melt("Age", var_name="Scenario", value_name="Net Worth")
    
    color_scale = alt.Scale(
        domain=["Median Trajectory", "100% Cash", "Target Net Worth"],
        range=["#3182bd", "#7f8c8d", "#e74c3c"] 
    )
    
    lines = alt.Chart(df_lines).mark_line().encode(
        x=alt.X("Age:Q", axis=alt.Axis(tickMinStep=1, format="d")),
        y=alt.Y("Net Worth:Q", axis=alt.Axis(format="$,.0f")),
        color=alt.Color("Scenario:N", scale=color_scale, legend=alt.Legend(title="Trajectory", orient="bottom")),
        strokeDash=alt.condition(
            alt.datum.Scenario == "Target Net Worth",
            alt.value([5, 5]),
            alt.value([0])
        ),
        size=alt.condition(
            alt.datum.Scenario == "Median Trajectory",
            alt.value(3),
            alt.value(2)
        ),
        tooltip=[
            alt.Tooltip("Age:Q", title="Age"),
            alt.Tooltip("Scenario:N", title="Scenario"),
            alt.Tooltip("Net Worth:Q", format="$,.0f", title="Net Worth")
        ]
    )
    
    area = alt.Chart(df_results).mark_area(opacity=0.2, color="#3182bd").encode(
        x=alt.X("Age:Q"),
        y=alt.Y("Worst-Case:Q", title="Net Worth ($)"),
        y2="Best-Case:Q",
        tooltip=[
            alt.Tooltip("Age:Q", title="Age"),
            alt.Tooltip("Best-Case:Q", format="$,.0f", title="Best-Case (90th %ile)"),
            alt.Tooltip("Worst-Case:Q", format="$,.0f", title="Worst-Case (10th %ile)")
        ]
    )
    
    chart = (area + lines).interactive()
    st.altair_chart(chart, width='stretch')

    final_median = p50[-1]
    final_p10 = p10[-1]
    final_cash = cash_only_trajectory[-1]
    prob_success = np.mean(all_trajectories[:, -1] >= target_nw) * 100

    col_res1, col_res2, col_res3, col_res4 = st.columns(4)
    col_res1.metric("Probability of Reaching Goal", f"{prob_success:.1f}%")
    col_res2.metric(f"Median Outcome (Age {target_age})", f"${final_median:,.0f}")
    col_res3.metric("Downside Stress Floor (10th %ile)", f"${final_p10:,.0f}")
    col_res4.metric("If You Only Invested in Cash", f"${final_cash:,.0f}")

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
    if "Today's Value" in display_mode:
        final_net_worths = final_net_worths / ((1 + inflation_rate) ** (num_steps - 1))
        
    hist_counts, bin_edges = np.histogram(final_net_worths, bins=40)
    bin_mids = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_labels = [f"${x/1000000:.1f}M" for x in bin_mids]
    
    df_hist = pd.DataFrame({
        "Frequency": hist_counts,
        "Net Worth Bracket": bin_labels
    }).set_index("Net Worth Bracket")
    
    st.bar_chart(df_hist)
