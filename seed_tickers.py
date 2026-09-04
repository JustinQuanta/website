import sqlite3
import os

# Define the path to the database. This script assumes it's run from the project root.
DB_PATH = os.path.join('instance', 'financial_data.db')

# --- This is the central list of all stocks you want to track ---
# You can easily add, remove, or move tickers between categories here.
TICKERS_BY_MARKET = {
    'US': [
        'AAPL', 'ADBE', 'AMD', 'AMZN', 'AVGO', 'BABA', 'BAC', 'BRK-B', 'CELH', 'CMG', 'COST', 'CRM', 
        'CRWD', 'DIS', 'DOCU', 'ENPH', 'FVRR', 'FTNT', 'GOOG', 'GOOGL', 'GRAB', 'HD', 'INTC','JD', 
        'JNJ', 'KO','KR', 'LULU', 'MA', 'MELI', 'META', 'MSFT', 'NKE', 'NOW','NVDA', 'NVO', 'PATH', 
        'PG', 'PLTR', 'SBUX', 'SHOP', 'T', 'TCEHY', 'TSLA', 'TSM', 'UNH', 'V', 'WMT'
    ]#,
    #'HK': [
    #    '1299.HK', '1788.HK', '9988.HK', '2828.HK', '2800.HK', '3033.HK', 
    #    '1810.HK', '3690.HK', '0700.HK', '0981.HK'
    #],
    #'SG': [
    #    'C2PU.SI', 'C6L.SI', 'HMN.SI', 'JYEU.SI', 'ME8U.SI'
    #]
    # You can add new markets like this:
    # 'LSE': ['ULVR.L', 'AZN.L']
}

def seed_database():
    """Inserts the predefined tickers into the tracked_tickers table."""
    
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file not found at '{DB_PATH}'.")
        print("Please run 'python setup_database.py' first.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        print(f"Connected to database at '{DB_PATH}'...")
        
        total_inserted = 0
        
        # The "INSERT OR IGNORE" command is safe to run multiple times.
        # It will only add tickers that don't already exist.
        sql = "INSERT OR IGNORE INTO tracked_tickers (ticker_symbol, market) VALUES (?, ?)"
        
        for market, tickers in TICKERS_BY_MARKET.items():
            print(f"\nSeeding tickers for market: {market}")
            for ticker in tickers:
                cursor.execute(sql, (ticker, market))
                # cursor.rowcount will be 1 if a new row was inserted, 0 otherwise.
                if cursor.rowcount > 0:
                    print(f"  -> Added '{ticker}'")
                    total_inserted += 1

        conn.commit()
        print(f"\nSeeding complete. Added {total_inserted} new tickers.")
        if total_inserted == 0:
            print("No new tickers were added (they may already exist in the database).")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")

if __name__ == '__main__':
    seed_database()

