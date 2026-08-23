terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

locals {
  tags = {
    Project   = "finance-capybara"
    ManagedBy = "terraform"
  }
}

data "aws_iam_policy_document" "finance_capybara_kms" {
  statement {
    sid    = "EnableRootAccountPermissions"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions   = ["kms:*"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowSQSAndDynamoDBUse"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["sqs.amazonaws.com", "dynamodb.amazonaws.com"]
    }

    actions = [
      "kms:CreateGrant",
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:ListGrants",
      "kms:RevokeGrant"
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_kms_key" "finance_capybara" {
  description             = "Encryption key for Finance Capybara SQS and DynamoDB resources"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.finance_capybara_kms.json

  tags = merge(local.tags, {
    Name = "finance-capybara-kms"
  })
}

resource "aws_kms_alias" "finance_capybara" {
  name          = "alias/finance-capybara-kms"
  target_key_id = aws_kms_key.finance_capybara.key_id
}

resource "aws_sqs_queue" "inbound_pivot_analysis_dlq" {
  name                              = "inbound-pivot-analysis-dlq"
  kms_master_key_id                 = aws_kms_key.finance_capybara.arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600

  tags = merge(local.tags, {
    Name = "inbound-pivot-analysis-dlq"
  })
}

resource "aws_sqs_queue" "inbout_pivot_analysis" {
  # Queue name intentionally matches the requested identifier.
  name                              = "inbout-pivot-analysis"
  kms_master_key_id                 = aws_kms_key.finance_capybara.arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.inbound_pivot_analysis_dlq.arn
    maxReceiveCount     = 5
  })

  tags = merge(local.tags, {
    Name = "inbout-pivot-analysis"
  })
}

resource "aws_dynamodb_table" "ticker_load_control" {
  name         = "ticker-load-control"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ticker"

  attribute {
    name = "ticker"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.finance_capybara.arn
  }

  tags = merge(local.tags, {
    Name = "ticker-load-control"
  })
}

output "kms_key_arn" {
  description = "ARN of the KMS key used to encrypt SQS and DynamoDB."
  value       = aws_kms_key.finance_capybara.arn
}

output "inbout_pivot_analysis_queue_url" {
  description = "URL of the inbound pivot analysis SQS queue."
  value       = aws_sqs_queue.inbout_pivot_analysis.url
}

output "inbound_pivot_analysis_dlq_url" {
  description = "URL of the inbound pivot analysis dead-letter queue."
  value       = aws_sqs_queue.inbound_pivot_analysis_dlq.url
}

output "ticker_load_control_table_name" {
  description = "Name of the DynamoDB table keyed by ticker symbols such as AAPL and AMZN."
  value       = aws_dynamodb_table.ticker_load_control.name
}
