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
      description  = "Detects SMA pivots for enabled tickers and publishes ticker interest to SQS."
      package_type = "Image"
      environment = {
        ENABLED_TICKERS_BUCKET = aws_s3_bucket.data["enabled_tickers"].id
        INBOUND_QUEUE_URL      = aws_sqs_queue.inbound_ticker_interest.url
        KMS_KEY_ARN            = aws_kms_key.master.arn
      }
    }
    aggregator = {
      description  = "Consumes ticker interest from SQS and writes aggregated daily runs."
      package_type = "Zip"
      environment = {
        INBOUND_QUEUE_URL      = aws_sqs_queue.inbound_ticker_interest.url
        AGGREGATED_RUNS_BUCKET = aws_s3_bucket.data["aggregated_daily_runs"].id
        KMS_KEY_ARN            = aws_kms_key.master.arn
      }
    }
  }

  zip_functions = { for k, v in local.functions : k => v if v.package_type == "Zip" }
}
