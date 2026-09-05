data "archive_file" "lambda" {
  for_each = local.functions

  type        = "zip"
  source_dir  = "${path.module}/lambda/${each.key}"
  output_path = "${path.module}/.terraform/${local.prefix}-${each.key}.zip"
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.functions

  # Custom log group names keep the requested fcb prefix.
  name              = "${local.prefix}-${each.key}-logs"
  retention_in_days = 30
  tags              = { Name = "${local.prefix}-${each.key}-logs" }
}

resource "aws_lambda_function" "worker" {
  for_each = local.functions

  function_name    = "${local.prefix}-${each.key}"
  description      = each.value.description
  role             = aws_iam_role.lambda[each.key].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.lambda[each.key].output_path
  source_code_hash = data.archive_file.lambda[each.key].output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = each.value.environment
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda[each.key].name
  }

  tags = { Name = "${local.prefix}-${each.key}" }

  depends_on = [
    aws_iam_role_policy.logs,
    aws_iam_role_policy.pivoter,
    aws_iam_role_policy.aggregator,
  ]
}
