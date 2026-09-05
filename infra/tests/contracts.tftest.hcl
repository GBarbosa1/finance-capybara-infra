# All AWS operations are mocked. No AWS credentials, backend or resources used.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_kms_key" {
    defaults = {
      arn    = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000001"
      key_id = "00000000-0000-0000-0000-000000000001"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/fcb-test-role" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:us-east-1:123456789012:fcb-inbound-ticker-interest"
      url = "https://sqs.us-east-1.amazonaws.com/123456789012/fcb-inbound-ticker-interest"
    }
  }
}

override_resource {
  target = aws_s3_bucket.data["enabled_tickers"]
  values = {
    id  = "fcb-enabled-tickers"
    arn = "arn:aws:s3:::fcb-enabled-tickers"
  }
}

override_resource {
  target = aws_s3_bucket.data["aggregated_daily_runs"]
  values = {
    id  = "fcb-aggregated-daily-runs"
    arn = "arn:aws:s3:::fcb-aggregated-daily-runs"
  }
}

run "resource_and_permission_contracts" {
  command = apply

  assert {
    condition = (
      aws_kms_alias.master.name == "alias/fcb-master-key" &&
      aws_kms_key.master.enable_key_rotation &&
      aws_sqs_queue.inbound_ticker_interest.name == "fcb-inbound-ticker-interest" &&
      aws_sqs_queue.inbound_ticker_interest.kms_master_key_id == aws_kms_key.master.arn &&
      aws_s3_bucket.data["enabled_tickers"].bucket == "fcb-enabled-tickers" &&
      aws_s3_bucket.data["aggregated_daily_runs"].bucket == "fcb-aggregated-daily-runs" &&
      alltrue([for name, worker in aws_lambda_function.worker : worker.function_name == "fcb-${name}"]) &&
      alltrue([for name, role in aws_iam_role.lambda : role.name == "fcb-${name}-role"])
    )
    error_message = "Resource names, key rotation and SQS encryption must match the infrastructure contract."
  }

  assert {
    condition = alltrue([
      for encryption in aws_s3_bucket_server_side_encryption_configuration.data : alltrue([
        for rule in encryption.rule : alltrue([
          for setting in rule.apply_server_side_encryption_by_default :
          setting.sse_algorithm == "aws:kms" && setting.kms_master_key_id == aws_kms_key.master.arn
        ])
      ])
      ]) && alltrue([
      for block in aws_s3_bucket_public_access_block.data :
      block.block_public_acls && block.block_public_policy && block.ignore_public_acls && block.restrict_public_buckets
    ])
    error_message = "Both buckets must be private and encrypted with the master key."
  }

  assert {
    condition = alltrue([
      for policy in aws_s3_bucket_policy.data : alltrue([
        for statement in jsondecode(policy.policy).Statement : statement.Effect == "Deny"
        ]) && length([
        for statement in jsondecode(policy.policy).Statement : statement
        if try(statement.Condition.StringNotEquals["s3:x-amz-server-side-encryption-aws-kms-key-id"], "") == aws_kms_key.master.arn
      ]) == 1
    ])
    error_message = "Bucket policies must reject uploads that do not select the master key."
  }

  assert {
    condition = (
      toset(flatten([for s in jsondecode(aws_iam_role_policy.pivoter.policy).Statement : s.Action])) == toset([
        "s3:ListBucket", "s3:GetObject", "sqs:SendMessage", "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"
        ]) && alltrue([
        for s in jsondecode(aws_iam_role_policy.pivoter.policy).Statement :
        s.Effect == "Allow" && alltrue([
          for action in s.Action :
          startswith(action, "kms:") ? s.Resource == aws_kms_key.master.arn :
          startswith(action, "sqs:") ? s.Resource == aws_sqs_queue.inbound_ticker_interest.arn :
          action == "s3:ListBucket" ? s.Resource == aws_s3_bucket.data["enabled_tickers"].arn :
          s.Resource == "${aws_s3_bucket.data["enabled_tickers"].arn}/*"
        ])
      ])
    )
    error_message = "Pivoter may only read enabled tickers, send queue messages and use the master key."
  }

  assert {
    condition = (
      toset(flatten([for s in jsondecode(aws_iam_role_policy.aggregator.policy).Statement : s.Action])) == toset([
        "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility",
        "s3:PutObject", "s3:AbortMultipartUpload", "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"
        ]) && alltrue([
        for s in jsondecode(aws_iam_role_policy.aggregator.policy).Statement :
        s.Effect == "Allow" && alltrue([
          for action in s.Action :
          startswith(action, "kms:") ? s.Resource == aws_kms_key.master.arn :
          startswith(action, "sqs:") ? s.Resource == aws_sqs_queue.inbound_ticker_interest.arn :
          s.Resource == "${aws_s3_bucket.data["aggregated_daily_runs"].arn}/*"
        ])
      ])
    )
    error_message = "Aggregator may only consume its queue, write aggregated runs and use the master key."
  }

  assert {
    condition = alltrue([
      for name, worker in aws_lambda_function.worker :
      worker.role == aws_iam_role.lambda[name].arn &&
      worker.environment[0].variables["KMS_KEY_ARN"] == aws_kms_key.master.arn &&
      worker.logging_config[0].log_group == aws_cloudwatch_log_group.lambda[name].name
      ]) && alltrue([
      for role in aws_iam_role.lambda :
      jsondecode(role.assume_role_policy).Statement[0].Principal.Service == "lambda.amazonaws.com"
    ])
    error_message = "Each Lambda must use its assigned execution role, master key and log group."
  }
}

run "globally_unique_bucket_names" {
  command = plan
  variables {
    bucket_name_suffix = "-123456789012"
  }
  assert {
    condition = (
      aws_s3_bucket.data["enabled_tickers"].bucket == "fcb-enabled-tickers-123456789012" &&
      aws_s3_bucket.data["aggregated_daily_runs"].bucket == "fcb-aggregated-daily-runs-123456789012"
    )
    error_message = "The optional suffix must apply consistently to both buckets."
  }
}

run "reject_invalid_bucket_names" {
  command = plan
  variables {
    bucket_name_suffix = "-INVALID SUFFIX"
  }
  expect_failures = [var.bucket_name_suffix]
}
