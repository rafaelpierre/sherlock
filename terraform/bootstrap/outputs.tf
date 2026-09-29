output "aws_account_id" {
  description = "AWS account ID"
  value       = data.aws_caller_identity.current.account_id
}

output "terraform_state_bucket" {
  description = "S3 bucket containing Terraform state"
  value       = aws_s3_bucket.terraform_state.bucket
}

output "github_oidc_provider_arn" {
  description = "GitHub Actions OIDC provider ARN"
  value       = aws_iam_openid_connect_provider.github.arn
}

output "github_terraform_role_arn" {
  description = "IAM role that GitHub Actions should assume"
  value       = aws_iam_role.terraform_github.arn
}

output "ecr_repository_urls" {
  description = "Application repositories that the deployment workflow pushes to"
  value       = { for name, repository in aws_ecr_repository.application : name => repository.repository_url }
}
