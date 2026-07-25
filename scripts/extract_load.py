import os
import sys
import time
import requests
import snowflake.connector
from dotenv import load_dotenv

# Explicit path so it works both locally and inside the Airflow container
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(env_path)

TICKERS = ["AAPL", "MSFT", "GOOGL", "JPM", "GS", "SPY"]
API_KEY = os.environ["ALPHAVANTAGE_API_KEY"]


def fetch_daily_prices(ticker):
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker,
        "outputsize": "compact",  # last ~100 trading days
        "apikey": API_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("Time Series (Daily)", {})

    records = []
    for trade_date, bar in data.items():
        records.append({
            "ticker": ticker,
            "trade_date": trade_date,
            "open_price": float(bar["1. open"]),
            "high_price": float(bar["2. high"]),
            "low_price": float(bar["3. low"]),
            "close_price": float(bar["4. close"]),
            "volume": int(bar["5. volume"]),
        })
    return records


def load_to_snowflake(records):
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
    )
    cur = conn.cursor()
    try:
        for r in records:
            cur.execute(
                """
                INSERT INTO DAILY_PRICES
                (ticker, trade_date, open_price, high_price, low_price, close_price, volume)
                VALUES (%(ticker)s, %(trade_date)s, %(open_price)s, %(high_price)s,
                        %(low_price)s, %(close_price)s, %(volume)s)
                """,
                r,
            )
        conn.commit()
        print(f"Loaded {len(records)} rows")
    finally:
        cur.close()
        conn.close()


def run():
    all_records = []
    for ticker in TICKERS:
        print(f"Fetching {ticker}...")
        all_records.extend(fetch_daily_prices(ticker))
        time.sleep(13)
    load_to_snowflake(all_records)
    print("Done.")
    # No sys.exit() here — this function is called directly by Airflow's
    # PythonOperator, and raising SystemExit here would make Airflow mark
    # the task as failed even though the data loaded successfully.


if __name__ == "__main__":
    # This block only runs when you execute the script directly from your
    # own terminal (python scripts/extract_load.py) — not when Airflow
    # imports and calls run() itself. Safe to force-exit here so your
    # terminal prompt returns immediately instead of hanging on a lingering
    # Snowflake connector thread.
    run()
    sys.exit(0)