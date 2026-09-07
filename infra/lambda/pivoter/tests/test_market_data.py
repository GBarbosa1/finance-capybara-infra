from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from market_data import fetch_daily_closes


def _fake_history(rows=60):
    index = pd.date_range(end=pd.Timestamp.now("UTC").normalize(), periods=rows, freq="D")
    return pd.DataFrame({"Close": range(rows)}, index=index)


@patch("market_data.yf.Ticker")
def test_fetch_daily_closes_returns_data(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = _fake_history()
    mock_ticker_cls.return_value = mock_ticker

    data = fetch_daily_closes("AAPL", sma_window=20, lookback_days=30)

    assert not data.empty
    mock_ticker_cls.assert_called_once_with("AAPL")
    assert mock_ticker.history.call_args.kwargs["interval"] == "1d"


@patch("market_data.yf.Ticker")
def test_fetch_daily_closes_raises_on_empty_result(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    mock_ticker_cls.return_value = mock_ticker

    with pytest.raises(ValueError, match="No market data returned for ticker 'BADTICK'"):
        fetch_daily_closes("BADTICK", sma_window=20, lookback_days=30)
