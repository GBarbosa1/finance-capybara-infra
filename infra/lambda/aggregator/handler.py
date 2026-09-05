"""Deployment scaffold for fcb-aggregator; do not acknowledge unprocessed messages."""


def lambda_handler(event, context):
    raise NotImplementedError(
        "Implement SQS processing and KMS-encrypted S3 writes before adding a queue trigger."
    )
