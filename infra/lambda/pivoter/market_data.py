import datetime as dt

import pandas as pd
import yfinance as yf


def fetch_daily_closes(ticker: str, sma_window: int, lookback_days: int) -> pd.DataFrame:
    trading_days_needed = sma_window + lookback_days
    calendar_days_needed = int(trading_days_needed * 1.6) + 15

    end = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=calendar_days_needed)

    data = yf.Ticker(ticker).history(start=start, end=end, interval="1d")
    if data.empty:
        raise ValueError(f"No market data returned for ticker '{ticker}'")

    return data
