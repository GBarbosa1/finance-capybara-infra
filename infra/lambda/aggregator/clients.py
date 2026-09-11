import boto3

_s3 = None
_sns = None


def s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def sns_client():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns")
    return _sns
