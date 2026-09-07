import pandas as pd

from pivot import compute_sma, find_recent_pivot


def _series(values, start="2024-01-01"):
    index = pd.date_range(start=start, periods=len(values), freq="D")
    return pd.Series(values, index=index)


def test_compute_sma_basic():
    closes = _series([1, 2, 3, 4, 5])
    sma = compute_sma(closes, sma_window=2)
    assert sma.iloc[-1] == 4.5
    assert pd.isna(sma.iloc[0])


def test_find_recent_pivot_detects_down_to_up_reversal():
    sma = _series([10, 9, 8, 7, 6, 5, 6, 7, 8, 9])
    pivot_date = find_recent_pivot(sma, lookback_days=30)
    assert pivot_date == sma.index[6]


def test_find_recent_pivot_returns_most_recent_reversal():
    sma = _series([10, 8, 6, 8, 10, 8, 6, 8, 10])
    pivot_date = find_recent_pivot(sma, lookback_days=30)
    assert pivot_date == sma.index[7]


def test_find_recent_pivot_none_when_monotonic_uptrend():
    sma = _series([1, 2, 3, 4, 5])
    assert find_recent_pivot(sma, lookback_days=30) is None


def test_find_recent_pivot_none_when_still_falling():
    sma = _series([5, 4, 3, 2, 1])
    assert find_recent_pivot(sma, lookback_days=30) is None


def test_find_recent_pivot_ignores_flat_days():
    sma = _series([10, 8, 6, 6, 6, 7, 8])
    pivot_date = find_recent_pivot(sma, lookback_days=30)
    assert pivot_date == sma.index[5]


def test_find_recent_pivot_respects_lookback_window():
    values = [10, 8, 6] + [7] * 40
    sma = _series(values)
    assert find_recent_pivot(sma, lookback_days=5) is None


def test_find_recent_pivot_none_when_too_short():
    sma = _series([1, 2])
    assert find_recent_pivot(sma, lookback_days=30) is None
