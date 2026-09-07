resource "aws_iam_role" "deployer" {
  name               = "pivoter-github-actions-deployer"
  assume_role_policy = file("${path.module}/../.github/aws-deployer-trust-policy.json")
  tags               = { Name = "pivoter-github-actions-deployer" }
}

resource "aws_iam_role_policy" "deployer" {
  name   = "pivoter-github-actions-deployer"
  role   = aws_iam_role.deployer.id
  policy = file("${path.module}/../.github/aws-deployer-permissions-policy.json")
}
