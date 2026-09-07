resource "aws_ecr_repository" "pivoter" {
  name                 = "${local.prefix}-pivoter"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${local.prefix}-pivoter" }
}

resource "aws_ecr_lifecycle_policy" "pivoter" {
  repository = aws_ecr_repository.pivoter.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
