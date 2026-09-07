variable "aws_region" {
  description = "AWS region; matches the existing GitHub deployment workflow."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name_suffix" {
  description = "Optional suffix for globally unique S3 names, e.g. -123456789012. Empty preserves the requested names."
  type        = string
  default     = ""

  validation {
    condition     = var.bucket_name_suffix == "" || can(regex("^-[a-z0-9]([a-z0-9-]{0,35}[a-z0-9])?$", var.bucket_name_suffix))
    error_message = "Use an empty suffix or a hyphen followed by 1-37 lowercase letters, numbers or hyphens; end with a letter or number."
  }
}

variable "pivoter_image_tag" {
  description = "Image tag in the fcb-pivoter ECR repository to deploy. The deploy workflow sets this to the built image's commit SHA; the default is a placeholder for local plan/validate only."
  type        = string
  default     = "bootstrap"
}
