import datetime as dt
import json
import os

from clients import s3_client, sqs_client
from market_data import fetch_daily_closes
from pivot import compute_sma, find_recent_pivot

DEFAULT_LOOKBACK_DAYS = 30


def _load_enabled_tickers(bucket: str):
    configs = []
    paginator = s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            body = s3_client().get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            configs.append(json.loads(body))
    return configs


def _check_ticker(config: dict):
    ticker = config["ticker"]
    sma_window = int(config["sma_window"])
    lookback_days = int(config.get("lookback_days", DEFAULT_LOOKBACK_DAYS))

    data = fetch_daily_closes(ticker, sma_window, lookback_days)
    sma = compute_sma(data["Close"], sma_window)
    return find_recent_pivot(sma, lookback_days)


def lambda_handler(event, context):
    bucket = os.environ["ENABLED_TICKERS_BUCKET"]
    queue_url = os.environ["INBOUND_QUEUE_URL"]

    checked = 0
    pivots_detected = []
    errors = []

    for config in _load_enabled_tickers(bucket):
        ticker = config.get("ticker")
        checked += 1
        try:
            pivot_date = _check_ticker(config)
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            continue

        if pivot_date is None:
            continue

        message = {
            "datetime": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ticker": ticker,
            "pivot_date": pivot_date.strftime("%Y-%m-%d"),
        }
        sqs_client().send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
        pivots_detected.append(message)

    return {"checked": checked, "pivots_detected": pivots_detected, "errors": errors}
