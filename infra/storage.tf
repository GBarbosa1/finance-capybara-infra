resource "aws_s3_bucket" "data" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = false
  tags          = { Name = each.value }
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each = aws_s3_bucket.data

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.master.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id
  # Uploaders must explicitly select this key; see README for the upload command.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [each.value.arn, "${each.value.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "RequireKMSEncryption"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${each.value.arn}/*"
        Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" } }
      },
      {
        Sid       = "RequireMasterKey"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${each.value.arn}/*"
        Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = aws_kms_key.master.arn } }
      }
    ]
  })
}

resource "aws_sqs_queue" "inbound_ticker_interest" {
  name                              = "${local.prefix}-inbound-ticker-interest"
  kms_master_key_id                 = aws_kms_key.master.arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600
  receive_wait_time_seconds         = 20
  # Six times the aggregator's 60-second timeout for a future SQS trigger.
  visibility_timeout_seconds = 360

  tags = { Name = "${local.prefix}-inbound-ticker-interest" }
}
