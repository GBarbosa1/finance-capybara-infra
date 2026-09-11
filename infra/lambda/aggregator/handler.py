import datetime as dt
import io
import json
import os
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from clients import s3_client, sns_client


def _parse_record(record):
    body = json.loads(record["body"])
    return {
        "datetime": body["datetime"],
        "ticker": body["ticker"],
        "pivot_date": body["pivot_date"],
    }


def _partition_key(now):
    return f"year={now:%Y}/month={now:%m}/day={now:%d}/{now.strftime('%H%M%S')}-{uuid.uuid4().hex}.parquet"


def lambda_handler(event, context):
    bucket = os.environ["AGGREGATED_RUNS_BUCKET"]
    kms_key_arn = os.environ["KMS_KEY_ARN"]
    topic_arn = os.environ["OUTBOUND_TOPIC_ARN"]

    records = []
    for raw in event.get("Records", []):
        try:
            records.append(_parse_record(raw))
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"Skipping malformed message {raw.get('messageId')}: {exc}")

    if not records:
        return {"aggregated": 0}

    table = pa.Table.from_pylist(records)
    buf = io.BytesIO()
    pq.write_table(table, buf)

    key = _partition_key(dt.datetime.now(dt.timezone.utc))
    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=buf.getvalue(),
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key_arn,
    )

    tickers = ", ".join(sorted({r["ticker"] for r in records}))
    sns_client().publish(
        TopicArn=topic_arn,
        Message=f"{len(records)} pivot(s) detected: {tickers}",
        Subject="Ticker pivot alert",
    )

    return {"aggregated": len(records), "s3_key": key}
