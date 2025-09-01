import os
import sqlite3
from website import create_app, db

print("--- Initializing application for database setup ---")
app = create_app()

# The 'with' block gives us access to the app's instance path
with app.app_context():
    # --- Step 1: Set up the User Database (app.db) ---
    app_db_path = os.path.join(app.instance_path, 'app.db')
    print(f"\nStep 1: Creating User tables in '{app_db_path}'...")
    
    # This command uses the app's config to create 'user' and 'note' tables in app.db
    db.create_all()
    print(" -> SQLAlchemy tables created successfully.")

    # --- Step 2: Set up the Financial Database (financial_data.db) ---
    financial_db_path = os.path.join(app.instance_path, 'financial_data.db')
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    print(f"\nStep 2: Creating Financial tables from schema.sql in '{financial_db_path}'...")

    if not os.path.exists(schema_path):
        print(f" [ERROR] schema.sql not found at '{schema_path}'. Please ensure it's in the project root.")
    else:
        try:
            # Connect directly to the financial database file
            conn = sqlite3.connect(financial_db_path)
            with open(schema_path, 'r') as f:
                conn.executescript(f.read())
            conn.close()
            print(" -> Financial tables created successfully.")
        except Exception as e:
            print(f" [ERROR] Failed to create financial tables: {e}")

print("\n--- Database setup complete. ---")