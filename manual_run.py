import os
from website import create_app
from website.data_jobs import run_data_collection
from website.data_jobs2 import run_data_collection_finite_horizon

# Optional: manual parameters for finite horizon
FH_PROJECTION_YEARS = 10
FH_DISCOUNT_RATE = 0.075
FH_MARGIN_OF_SAFETY = 0.20

def get_tracked_tickers(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT ticker_symbol FROM tracked_tickers WHERE is_active = 1").fetchall()
    tickers = [row[0] for row in rows]
    conn.close()
    return tickers

print("Initializing Flask app for context...")
app = create_app()

with app.app_context():
    db_path = os.path.join(app.instance_path, 'financial_data.db')
    tickers = get_tracked_tickers(db_path)

    # --- WACC/Perpetual Growth Model ---
    print("--- STARTING MANUAL DATA COLLECTION (WACC/perpetual) ---")
    run_data_collection(db_path)
    print("--- MANUAL DATA COLLECTION (WACC/perpetual) FINISHED ---")

    # --- Finite Horizon Model ---
    print("--- STARTING MANUAL DATA COLLECTION (Finite Horizon) ---")
    run_data_collection_finite_horizon(
        db_path,
        tickers,
        projection_years=FH_PROJECTION_YEARS,
        discount_rate=FH_DISCOUNT_RATE,
        margin_of_safety=FH_MARGIN_OF_SAFETY
    )
    print("--- MANUAL DATA COLLECTION (Finite Horizon) FINISHED ---")