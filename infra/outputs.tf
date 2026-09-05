output "kms_key_arn" {
  description = "KMS key ARN; use this for explicit SSE-KMS uploads."
  value       = aws_kms_key.master.arn
}

output "kms_alias" {
  value = aws_kms_alias.master.name
}

output "enabled_tickers_bucket" {
  value = aws_s3_bucket.data["enabled_tickers"].id
}

output "aggregated_daily_runs_bucket" {
  value = aws_s3_bucket.data["aggregated_daily_runs"].id
}

output "inbound_ticker_interest_queue_url" {
  value = aws_sqs_queue.inbound_ticker_interest.url
}

output "lambda_arns" {
  value = { for name, worker in aws_lambda_function.worker : name => worker.arn }
}

output "lambda_role_arns" {
  value = { for name, role in aws_iam_role.lambda : name => role.arn }
}
