terraform {
  required_version = ">= 1.10.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "Terraform"
    }
  }
}

data "aws_ecr_repository" "frontend" {
  name = "${var.project_name}-frontend"
}

data "aws_ecr_repository" "backend" {
  name = "${var.project_name}-backend"
}

data "aws_ecr_repository" "mcp" {
  name = "${var.project_name}-mcp"
}

# The value is intentionally populated only by the manually-dispatched GitHub
# workflow. Terraform owns the secret container, never the Phoenix credentials.
resource "aws_secretsmanager_secret" "phoenix_otel" {
  name                    = "${var.project_name}-phoenix-otel"
  recovery_window_in_days = 7
}

module "frontend" {
  source = "./modules/ecs_express"

  project_name       = var.project_name
  aws_region         = var.aws_region
  service_name       = "${var.project_name}-frontend"
  image_uri          = "${data.aws_ecr_repository.frontend.repository_url}:${var.frontend_image_tag}"
  container_port     = 80
  health_check_path  = "/health"
  cpu                = "256"
  memory             = "512"
  minimum_task_count = 1
  maximum_task_count = 1
  backend_url        = module.backend.endpoint
}

module "backend" {
  source = "./modules/ecs_express"

  project_name               = var.project_name
  aws_region                 = var.aws_region
  service_name               = "${var.project_name}-backend"
  image_uri                  = "${data.aws_ecr_repository.backend.repository_url}:${var.backend_image_tag}"
  container_port             = 8080
  health_check_path          = "/v1/health"
  cpu                        = "512"
  memory                     = "1024"
  minimum_task_count         = 1
  maximum_task_count         = 1
  create_agentcore_task_role = true
  agentcore_runtime_arn      = module.agentcore.runtime_arn
  runtime_secret_arns        = [aws_secretsmanager_secret.phoenix_otel.arn]
  phoenix_secret_id          = aws_secretsmanager_secret.phoenix_otel.arn
}

module "agentcore" {
  source = "./modules/agentcore"

  project_name = var.project_name
  aws_region   = var.aws_region
  image_uri    = "${data.aws_ecr_repository.mcp.repository_url}:${var.mcp_image_tag}"
}
