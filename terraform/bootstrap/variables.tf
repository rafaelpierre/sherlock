variable "aws_region" {
  description = "AWS region to use"
  type        = string
  default     = "eu-west-2"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "github_owner" {
  description = "GitHub username or organisation"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
}
