resource "aws_iam_role" "lambda" {
  for_each = local.functions

  name = "${local.prefix}-${each.key}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Name = "${local.prefix}-${each.key}-role" }
}

resource "aws_iam_role_policy" "logs" {
  for_each = local.functions

  name = "${local.prefix}-${each.key}-logs"
  role = aws_iam_role.lambda[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.lambda[each.key].arn}:*"
    }]
  })
}

resource "aws_iam_role_policy" "pivoter" {
  name = "${local.prefix}-pivoter-access"
  role = aws_iam_role.lambda["pivoter"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListEnabledTickers"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.data["enabled_tickers"].arn
      },
      {
        Sid      = "ReadEnabledTickers"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.data["enabled_tickers"].arn}/*"
      },
      {
        Sid      = "PublishTickerInterest"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.inbound_ticker_interest.arn
      },
      {
        Sid      = "DecryptS3AndEncryptSQS"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.master.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "aggregator" {
  name = "${local.prefix}-aggregator-access"
  role = aws_iam_role.lambda["aggregator"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeTickerInterest"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage", "sqs:DeleteMessage",
          "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.inbound_ticker_interest.arn
      },
      {
        Sid      = "WriteAggregatedRuns"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.data["aggregated_daily_runs"].arn}/*"
      },
      {
        Sid      = "DecryptSQSAndEncryptS3"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.master.arn
      }
    ]
  })
}
