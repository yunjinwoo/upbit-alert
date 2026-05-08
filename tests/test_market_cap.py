from app.core.stock_monitor import fetch_market_cap_ranking, get_access_token
from app.utils.db_manager import init_db
import logging

if __name__ == "__main__":
    # Ensure DB is initialized
    init_db()
    
    # Force log to console
    logging.basicConfig(level=logging.INFO)
    
    print("Starting manual fetch...")
    token = get_access_token()
    if token:
        print(f"Token acquired: {token[:10]}...")
        fetch_market_cap_ranking()
        print("Fetch completed.")
    else:
        print("Failed to acquire token.")
