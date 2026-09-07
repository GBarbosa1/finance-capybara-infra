data "archive_file" "lambda" {
  for_each = local.zip_functions

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

  function_name = "${local.prefix}-${each.key}"
  description   = each.value.description
  role          = aws_iam_role.lambda[each.key].arn
  architectures = ["arm64"]
  timeout       = 60
  memory_size   = 256
  package_type  = each.value.package_type

  runtime          = each.value.package_type == "Zip" ? "python3.13" : null
  handler          = each.value.package_type == "Zip" ? "handler.lambda_handler" : null
  filename         = each.value.package_type == "Zip" ? try(data.archive_file.lambda[each.key].output_path, null) : null
  source_code_hash = each.value.package_type == "Zip" ? try(data.archive_file.lambda[each.key].output_base64sha256, null) : null

  image_uri = each.value.package_type == "Image" ? "${aws_ecr_repository.pivoter.repository_url}:${var.pivoter_image_tag}" : null

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
