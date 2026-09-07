import json
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

os.environ.setdefault("ENABLED_TICKERS_BUCKET", "fcb-enabled-tickers")
os.environ.setdefault("INBOUND_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789012/fcb-inbound-ticker-interest")

import handler  # noqa: E402


def _s3_stub(ticker_configs):
    s3 = MagicMock()
    contents = [{"Key": f"{cfg['ticker']}.json"} for cfg in ticker_configs]
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": contents}]
    s3.get_paginator.return_value = paginator

    bodies = {f"{cfg['ticker']}.json": json.dumps(cfg).encode() for cfg in ticker_configs}
    s3.get_object.side_effect = lambda Bucket, Key: {"Body": BytesIO(bodies[Key])}
    return s3


@pytest.fixture(autouse=True)
def reset_env():
    os.environ["ENABLED_TICKERS_BUCKET"] = "fcb-enabled-tickers"
    os.environ["INBOUND_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/123456789012/fcb-inbound-ticker-interest"


@patch("handler.find_recent_pivot")
@patch("handler.compute_sma")
@patch("handler.fetch_daily_closes")
@patch("handler.sqs_client")
@patch("handler.s3_client")
def test_lambda_handler_publishes_for_ticker_with_pivot(
    mock_s3_client, mock_sqs_client, mock_fetch, mock_compute_sma, mock_find_pivot
):
    mock_s3_client.return_value = _s3_stub([{"ticker": "AAPL", "sma_window": 20}])
    mock_sqs = MagicMock()
    mock_sqs_client.return_value = mock_sqs
    mock_fetch.return_value = pd.DataFrame({"Close": [1, 2, 3]})
    mock_find_pivot.return_value = pd.Timestamp("2026-08-25")

    result = handler.lambda_handler({}, None)

    assert result["checked"] == 1
    assert result["errors"] == []
    assert len(result["pivots_detected"]) == 1
    message = result["pivots_detected"][0]
    assert message["ticker"] == "AAPL"
    assert message["pivot_date"] == "2026-08-25"
    assert "datetime" in message

    mock_sqs.send_message.assert_called_once()
    call_kwargs = mock_sqs.send_message.call_args.kwargs
    assert call_kwargs["QueueUrl"] == os.environ["INBOUND_QUEUE_URL"]
    assert json.loads(call_kwargs["MessageBody"]) == message


@patch("handler.find_recent_pivot")
@patch("handler.compute_sma")
@patch("handler.fetch_daily_closes")
@patch("handler.sqs_client")
@patch("handler.s3_client")
def test_lambda_handler_skips_publish_when_no_pivot(
    mock_s3_client, mock_sqs_client, mock_fetch, mock_compute_sma, mock_find_pivot
):
    mock_s3_client.return_value = _s3_stub([{"ticker": "MSFT", "sma_window": 20}])
    mock_sqs = MagicMock()
    mock_sqs_client.return_value = mock_sqs
    mock_fetch.return_value = pd.DataFrame({"Close": [1, 2, 3]})
    mock_find_pivot.return_value = None

    result = handler.lambda_handler({}, None)

    assert result == {"checked": 1, "pivots_detected": [], "errors": []}
    mock_sqs.send_message.assert_not_called()


@patch("handler.find_recent_pivot")
@patch("handler.compute_sma")
@patch("handler.fetch_daily_closes")
@patch("handler.sqs_client")
@patch("handler.s3_client")
def test_lambda_handler_continues_after_one_ticker_fails(
    mock_s3_client, mock_sqs_client, mock_fetch, mock_compute_sma, mock_find_pivot
):
    mock_s3_client.return_value = _s3_stub(
        [{"ticker": "BADTICK", "sma_window": 20}, {"ticker": "AAPL", "sma_window": 20}]
    )
    mock_sqs = MagicMock()
    mock_sqs_client.return_value = mock_sqs

    def fetch_side_effect(ticker, sma_window, lookback_days):
        if ticker == "BADTICK":
            raise ValueError("No market data returned for ticker 'BADTICK'")
        return pd.DataFrame({"Close": [1, 2, 3]})

    mock_fetch.side_effect = fetch_side_effect
    mock_find_pivot.return_value = pd.Timestamp("2026-08-25")

    result = handler.lambda_handler({}, None)

    assert result["checked"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["ticker"] == "BADTICK"
    assert len(result["pivots_detected"]) == 1
    assert result["pivots_detected"][0]["ticker"] == "AAPL"
    mock_sqs.send_message.assert_called_once()


@patch("handler.s3_client")
def test_lambda_handler_no_enabled_tickers(mock_s3_client):
    mock_s3_client.return_value = _s3_stub([])

    result = handler.lambda_handler({}, None)

    assert result == {"checked": 0, "pivots_detected": [], "errors": []}
