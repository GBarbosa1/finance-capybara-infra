from typing import Optional

import pandas as pd


def compute_sma(closes: pd.Series, sma_window: int) -> pd.Series:
    return closes.rolling(window=sma_window).mean()


def find_recent_pivot(sma: pd.Series, lookback_days: int) -> Optional[pd.Timestamp]:
    sma = sma.dropna()
    if len(sma) < 3:
        return None

    window = sma.iloc[-(lookback_days + 1):]
    directions = window.diff().dropna().apply(lambda d: 1 if d > 0 else (-1 if d < 0 else 0))

    pivot_date = None
    last_direction = 0
    for date, direction in directions.items():
        if direction == 0:
            continue
        if direction > 0 and last_direction < 0:
            pivot_date = date
        last_direction = direction

    return pivot_date
