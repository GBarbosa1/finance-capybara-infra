#!/usr/bin/env python3
"""Idempotently provisions and deploys the Finance Capybara pipeline: KMS key,
S3 buckets, SQS queue, Lambda IAM roles, log groups, the pivoter ECR
repository, and both Lambda functions. Safe to re-run; only creates what's
missing and updates what differs from the desired state below.

Usage: python deploy.py --pivoter-image-uri <ecr-repo-url>:<tag>
"""
import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
PREFIX = "fcb"
LAMBDA_DIR = Path(__file__).parent / "lambda"

session = boto3.Session(region_name=REGION)
sts = session.client("sts")
kms = session.client("kms")
s3 = session.client("s3")
sqs = session.client("sqs")
iam = session.client("iam")
logs = session.client("logs")
ecr = session.client("ecr")
lambda_ = session.client("lambda")

ACCOUNT_ID = sts.get_caller_identity()["Account"]
TAGS = {"Project": "finance-capybara", "ManagedBy": "deploy-script", "Environment": "production"}


def log(msg):
    print(f"==> {msg}", file=sys.stderr, flush=True)


def tags_block(name):
    return {**TAGS, "Name": name}


# --- KMS -----------------------------------------------------------------

def ensure_kms_key():
    alias_name = f"alias/{PREFIX}-master-key"
    try:
        key = kms.describe_key(KeyId=alias_name)["KeyMetadata"]
        key_arn = key["Arn"]
        log(f"KMS key exists: {key_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NotFoundException":
            raise
        key = kms.create_key(
            Description=f"{PREFIX}-master-key: encryption for Finance Capybara S3 and SQS data",
            Tags=[{"TagKey": k, "TagValue": v} for k, v in tags_block(f"{PREFIX}-master-key").items()],
        )["KeyMetadata"]
        key_arn = key["Arn"]
        kms.create_alias(AliasName=alias_name, TargetKeyId=key["KeyId"])
        log(f"Created KMS key {key_arn} with alias {alias_name}")

    rotation = kms.get_key_rotation_status(KeyId=key_arn)["KeyRotationEnabled"]
    if not rotation:
        kms.enable_key_rotation(KeyId=key_arn)
        log("Enabled KMS key rotation")

    kms.tag_resource(
        KeyId=key_arn,
        Tags=[{"TagKey": k, "TagValue": v} for k, v in tags_block(f"{PREFIX}-master-key").items()],
    )
    return key_arn


# --- S3 --------------------------------------------------------------------

def bucket_policy(bucket_name, key_arn):
    bucket_arn = f"arn:aws:s3:::{bucket_name}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
            {
                "Sid": "RequireKMSEncryption",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:PutObject",
                "Resource": f"{bucket_arn}/*",
                "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}},
            },
            {
                "Sid": "RequireMasterKey",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:PutObject",
                "Resource": f"{bucket_arn}/*",
                "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption-aws-kms-key-id": key_arn}},
            },
        ],
    }


def ensure_bucket(name, key_arn):
    try:
        s3.head_bucket(Bucket=name)
        log(f"S3 bucket exists: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
        # us-east-1 is the one region where create_bucket must omit CreateBucketConfiguration.
        s3.create_bucket(Bucket=name)
        log(f"Created S3 bucket: {name}")

    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_ownership_controls(
        Bucket=name, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
    )
    s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": key_arn,
                    },
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    s3.put_bucket_policy(Bucket=name, Policy=json.dumps(bucket_policy(name, key_arn)))
    s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags_block(name).items()]})


# --- SQS ---------------------------------------------------------------------

def ensure_queue(key_arn):
    name = f"{PREFIX}-inbound-ticker-interest"
    attributes = {
        "KmsMasterKeyId": key_arn,
        "KmsDataKeyReusePeriodSeconds": "300",
        "MessageRetentionPeriod": "1209600",
        "ReceiveMessageWaitTimeSeconds": "20",
        # Six times the aggregator's 60-second timeout for a future SQS trigger.
        "VisibilityTimeout": "360",
    }
    try:
        queue_url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        sqs.set_queue_attributes(QueueUrl=queue_url, Attributes=attributes)
        log(f"SQS queue exists: {queue_url}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "AWS.SimpleQueueService.NonExistentQueue":
            raise
        queue_url = sqs.create_queue(QueueName=name, Attributes=attributes)["QueueUrl"]
        log(f"Created SQS queue: {queue_url}")

    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sqs.tag_queue(QueueUrl=queue_url, Tags=tags_block(name))
    return queue_url, queue_arn


# --- IAM ---------------------------------------------------------------------

LAMBDA_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
}


def ensure_lambda_role(name, inline_policies):
    try:
        role_arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        log(f"IAM role exists: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        role_arn = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST_POLICY),
            Tags=[{"Key": k, "Value": v} for k, v in tags_block(name).items()],
        )["Role"]["Arn"]
        log(f"Created IAM role: {name}")

    for policy_name, statements in inline_policies.items():
        iam.put_role_policy(
            RoleName=name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}),
        )

    iam.tag_role(RoleName=name, Tags=[{"Key": k, "Value": v} for k, v in tags_block(name).items()])
    return role_arn


# --- CloudWatch Logs -----------------------------------------------------

def ensure_log_group(name):
    try:
        logs.create_log_group(logGroupName=name, tags=tags_block(name))
        log(f"Created log group: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
        log(f"Log group exists: {name}")
    log_group_arn = f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:{name}"
    logs.tag_resource(resourceArn=log_group_arn, tags=tags_block(name))
    logs.put_retention_policy(logGroupName=name, retentionInDays=30)
    return log_group_arn


# --- ECR -----------------------------------------------------------------

def ensure_ecr_repository():
    name = f"{PREFIX}-pivoter"
    try:
        repo = ecr.describe_repositories(repositoryNames=[name])["repositories"][0]
        log(f"ECR repository exists: {repo['repositoryUri']}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        repo = ecr.create_repository(
            repositoryName=name,
            imageTagMutability="IMMUTABLE",
            imageScanningConfiguration={"scanOnPush": True},
            tags=[{"Key": k, "Value": v} for k, v in tags_block(name).items()],
        )["repository"]
        log(f"Created ECR repository: {repo['repositoryUri']}")

    ecr.put_lifecycle_policy(
        repositoryName=name,
        lifecyclePolicyText=json.dumps(
            {
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": "Expire untagged images after 14 days",
                        "selection": {
                            "tagStatus": "untagged",
                            "countType": "sinceImagePushed",
                            "countUnit": "days",
                            "countNumber": 14,
                        },
                        "action": {"type": "expire"},
                    }
                ]
            }
        ),
    )
    return repo["repositoryUri"]


# --- Lambda ----------------------------------------------------------------

def zip_directory(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(directory))
    return buf.getvalue()


def ensure_zip_function(name, description, role_arn, environment, log_group_name):
    zip_bytes = zip_directory(LAMBDA_DIR / "aggregator")
    common = dict(
        FunctionName=name,
        Description=description,
        Role=role_arn,
        Timeout=60,
        MemorySize=256,
        Environment={"Variables": environment},
        LoggingConfig={"LogFormat": "Text", "LogGroup": log_group_name},
    )

    try:
        current = lambda_.get_function(FunctionName=name)["Configuration"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        current = None

    if current is None:
        lambda_.create_function(
            **common,
            Runtime="python3.13",
            Architectures=["arm64"],
            Handler="handler.lambda_handler",
            PackageType="Zip",
            Code={"ZipFile": zip_bytes},
            Tags=tags_block(name),
        )
        log(f"Created Lambda function: {name}")
        return

    if current["PackageType"] != "Zip":
        raise RuntimeError(f"{name} is currently PackageType={current['PackageType']}; manual migration required.")

    lambda_.update_function_code(FunctionName=name, ZipFile=zip_bytes)
    _wait_for_update(name)
    lambda_.update_function_configuration(**common)
    _wait_for_update(name)
    lambda_.tag_resource(Resource=current["FunctionArn"], Tags=tags_block(name))
    log(f"Updated Lambda function: {name}")


def ensure_image_function(name, description, role_arn, environment, log_group_name, image_uri):
    common = dict(
        FunctionName=name,
        Description=description,
        Role=role_arn,
        Timeout=60,
        MemorySize=256,
        Environment={"Variables": environment},
        LoggingConfig={"LogFormat": "Text", "LogGroup": log_group_name},
    )

    try:
        current = lambda_.get_function(FunctionName=name)["Configuration"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        current = None

    if current is not None and current["PackageType"] != "Image":
        log(f"{name} is currently PackageType={current['PackageType']}; deleting to switch to Image (AWS does not allow package_type to change in place).")
        lambda_.delete_function(FunctionName=name)
        current = None

    if current is None:
        lambda_.create_function(
            **common,
            Architectures=["arm64"],
            PackageType="Image",
            Code={"ImageUri": image_uri},
            Tags=tags_block(name),
        )
        log(f"Created Lambda function: {name}")
        return

    lambda_.update_function_code(FunctionName=name, ImageUri=image_uri)
    _wait_for_update(name)
    lambda_.update_function_configuration(**common)
    _wait_for_update(name)
    lambda_.tag_resource(Resource=current["FunctionArn"], Tags=tags_block(name))
    log(f"Updated Lambda function: {name}")


def _wait_for_update(name):
    waiter = lambda_.get_waiter("function_updated_v2")
    waiter.wait(FunctionName=name)


# --- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pivoter-image-uri",
        help="Full ECR image URI:tag to deploy for fcb-pivoter. Required unless --ecr-repo-only.",
    )
    parser.add_argument("--bucket-suffix", default=os.environ.get("BUCKET_NAME_SUFFIX", ""))
    parser.add_argument(
        "--ecr-repo-only",
        action="store_true",
        help="Only ensure the pivoter ECR repository exists, print its URI, and exit. "
        "Used before the image is built, so there's somewhere to push it.",
    )
    args = parser.parse_args()

    if args.ecr_repo_only:
        print(ensure_ecr_repository())
        return

    if not args.pivoter_image_uri:
        parser.error("--pivoter-image-uri is required unless --ecr-repo-only is set")

    key_arn = ensure_kms_key()

    enabled_tickers_bucket = f"{PREFIX}-enabled-tickers{args.bucket_suffix}"
    aggregated_runs_bucket = f"{PREFIX}-aggregated-daily-runs{args.bucket_suffix}"
    ensure_bucket(enabled_tickers_bucket, key_arn)
    ensure_bucket(aggregated_runs_bucket, key_arn)

    queue_url, queue_arn = ensure_queue(key_arn)

    ecr_repo_uri = ensure_ecr_repository()

    pivoter_log_group = ensure_log_group(f"{PREFIX}-pivoter-logs")
    aggregator_log_group = ensure_log_group(f"{PREFIX}-aggregator-logs")

    pivoter_role_arn = ensure_lambda_role(
        f"{PREFIX}-pivoter-role",
        {
            f"{PREFIX}-pivoter-logs": [
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": f"{pivoter_log_group}:*",
                }
            ],
            f"{PREFIX}-pivoter-access": [
                {
                    "Sid": "ListEnabledTickers",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": f"arn:aws:s3:::{enabled_tickers_bucket}",
                },
                {
                    "Sid": "ReadEnabledTickers",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": f"arn:aws:s3:::{enabled_tickers_bucket}/*",
                },
                {
                    "Sid": "PublishTickerInterest",
                    "Effect": "Allow",
                    "Action": ["sqs:SendMessage"],
                    "Resource": queue_arn,
                },
                {
                    "Sid": "DecryptS3AndEncryptSQS",
                    "Effect": "Allow",
                    "Action": ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"],
                    "Resource": key_arn,
                },
            ],
        },
    )

    aggregator_role_arn = ensure_lambda_role(
        f"{PREFIX}-aggregator-role",
        {
            f"{PREFIX}-aggregator-logs": [
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": f"{aggregator_log_group}:*",
                }
            ],
            f"{PREFIX}-aggregator-access": [
                {
                    "Sid": "ConsumeTickerInterest",
                    "Effect": "Allow",
                    "Action": [
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:ChangeMessageVisibility",
                    ],
                    "Resource": queue_arn,
                },
                {
                    "Sid": "WriteAggregatedRuns",
                    "Effect": "Allow",
                    "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
                    "Resource": f"arn:aws:s3:::{aggregated_runs_bucket}/*",
                },
                {
                    "Sid": "DecryptSQSAndEncryptS3",
                    "Effect": "Allow",
                    "Action": ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"],
                    "Resource": key_arn,
                },
            ],
        },
    )

    ensure_image_function(
        f"{PREFIX}-pivoter",
        "Detects SMA pivots for enabled tickers and publishes ticker interest to SQS.",
        pivoter_role_arn,
        {
            "ENABLED_TICKERS_BUCKET": enabled_tickers_bucket,
            "INBOUND_QUEUE_URL": queue_url,
            "KMS_KEY_ARN": key_arn,
        },
        f"{PREFIX}-pivoter-logs",
        args.pivoter_image_uri,
    )

    ensure_zip_function(
        f"{PREFIX}-aggregator",
        "Consumes ticker interest from SQS and writes aggregated daily runs.",
        aggregator_role_arn,
        {
            "INBOUND_QUEUE_URL": queue_url,
            "AGGREGATED_RUNS_BUCKET": aggregated_runs_bucket,
            "KMS_KEY_ARN": key_arn,
        },
        f"{PREFIX}-aggregator-logs",
    )

    log(f"Done. ECR repository: {ecr_repo_uri}")


if __name__ == "__main__":
    main()
