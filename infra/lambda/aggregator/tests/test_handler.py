import json
import os
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pytest

os.environ.setdefault("AGGREGATED_RUNS_BUCKET", "fcb-aggregated-daily-runs")
os.environ.setdefault("KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000")
os.environ.setdefault("OUTBOUND_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:fcb-outbound-ticker-notification")

import handler  # noqa: E402


def _sqs_record(message_id, body):
    return {"messageId": message_id, "body": json.dumps(body) if not isinstance(body, str) else body}


@pytest.fixture(autouse=True)
def reset_env():
    os.environ["AGGREGATED_RUNS_BUCKET"] = "fcb-aggregated-daily-runs"
    os.environ["KMS_KEY_ARN"] = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
    os.environ["OUTBOUND_TOPIC_ARN"] = "arn:aws:sns:us-east-1:123456789012:fcb-outbound-ticker-notification"


@patch("handler.sns_client")
@patch("handler.s3_client")
def test_lambda_handler_writes_parquet_and_notifies(mock_s3_client, mock_sns_client):
    mock_s3 = MagicMock()
    mock_s3_client.return_value = mock_s3
    mock_sns = MagicMock()
    mock_sns_client.return_value = mock_sns

    event = {
        "Records": [
            _sqs_record("1", {"datetime": "2026-09-07T14:32:01+00:00", "ticker": "AAPL", "pivot_date": "2026-08-25"}),
            _sqs_record("2", {"datetime": "2026-09-07T14:33:01+00:00", "ticker": "MSFT", "pivot_date": "2026-08-26"}),
        ]
    }

    result = handler.lambda_handler(event, None)

    assert result["aggregated"] == 2
    mock_s3.put_object.assert_called_once()
    put_kwargs = mock_s3.put_object.call_args.kwargs
    assert put_kwargs["Bucket"] == "fcb-aggregated-daily-runs"
    assert put_kwargs["ServerSideEncryption"] == "aws:kms"
    assert put_kwargs["SSEKMSKeyId"] == os.environ["KMS_KEY_ARN"]
    assert put_kwargs["Key"] == result["s3_key"]
    assert put_kwargs["Key"].count("/") == 3
    assert put_kwargs["Key"].startswith("year=")

    table = pq.read_table(__import__("io").BytesIO(put_kwargs["Body"]))
    assert sorted(table.column("ticker").to_pylist()) == ["AAPL", "MSFT"]

    mock_sns.publish.assert_called_once()
    publish_kwargs = mock_sns.publish.call_args.kwargs
    assert publish_kwargs["TopicArn"] == os.environ["OUTBOUND_TOPIC_ARN"]
    assert "AAPL" in publish_kwargs["Message"]
    assert "MSFT" in publish_kwargs["Message"]
    assert "2 pivot" in publish_kwargs["Message"]


@patch("handler.sns_client")
@patch("handler.s3_client")
def test_lambda_handler_skips_malformed_records(mock_s3_client, mock_sns_client):
    mock_s3 = MagicMock()
    mock_s3_client.return_value = mock_s3
    mock_sns = MagicMock()
    mock_sns_client.return_value = mock_sns

    event = {
        "Records": [
            _sqs_record("1", "not valid json"),
            _sqs_record("2", {"ticker": "AAPL"}),
            _sqs_record("3", {"datetime": "2026-09-07T14:32:01+00:00", "ticker": "AAPL", "pivot_date": "2026-08-25"}),
        ]
    }

    result = handler.lambda_handler(event, None)

    assert result["aggregated"] == 1
    mock_s3.put_object.assert_called_once()
    mock_sns.publish.assert_called_once()


@patch("handler.sns_client")
@patch("handler.s3_client")
def test_lambda_handler_no_valid_records_skips_write_and_notify(mock_s3_client, mock_sns_client):
    mock_s3 = MagicMock()
    mock_s3_client.return_value = mock_s3
    mock_sns = MagicMock()
    mock_sns_client.return_value = mock_sns

    result = handler.lambda_handler({"Records": [_sqs_record("1", "garbage")]}, None)

    assert result == {"aggregated": 0}
    mock_s3.put_object.assert_not_called()
    mock_sns.publish.assert_not_called()
