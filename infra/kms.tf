resource "aws_kms_key" "master" {
  description             = "fcb-master-key: encryption for Finance Capybara S3 and SQS data"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  # Enables account IAM policies; Lambda grants reference this specific key ARN.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableAccountIAMPermissions"
      Effect    = "Allow"
      Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  tags = { Name = "${local.prefix}-master-key" }
}

resource "aws_kms_alias" "master" {
  name          = "alias/${local.prefix}-master-key"
  target_key_id = aws_kms_key.master.key_id
}
