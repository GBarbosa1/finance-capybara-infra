data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  prefix = "fcb"
  buckets = {
    enabled_tickers       = "${local.prefix}-enabled-tickers${var.bucket_name_suffix}"
    aggregated_daily_runs = "${local.prefix}-aggregated-daily-runs${var.bucket_name_suffix}"
  }
  functions = {
    pivoter = {
      description = "Reads enabled ticker objects and publishes ticker interest to SQS."
      environment = {
        ENABLED_TICKERS_BUCKET = aws_s3_bucket.data["enabled_tickers"].id
        INBOUND_QUEUE_URL      = aws_sqs_queue.inbound_ticker_interest.url
        KMS_KEY_ARN            = aws_kms_key.master.arn
      }
    }
    aggregator = {
      description = "Consumes ticker interest from SQS and writes aggregated daily runs."
      environment = {
        INBOUND_QUEUE_URL      = aws_sqs_queue.inbound_ticker_interest.url
        AGGREGATED_RUNS_BUCKET = aws_s3_bucket.data["aggregated_daily_runs"].id
        KMS_KEY_ARN            = aws_kms_key.master.arn
      }
    }
  }
}
