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

module "frontend" {
  source = "./modules/ecs_express"

  project_name       = var.project_name
  aws_region         = var.aws_region
  service_name       = "${var.project_name}-frontend"
  image_uri          = "${data.aws_ecr_repository.frontend.repository_url}:${var.image_tag}"
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

  project_name          = var.project_name
  aws_region            = var.aws_region
  service_name          = "${var.project_name}-backend"
  image_uri             = "${data.aws_ecr_repository.backend.repository_url}:${var.image_tag}"
  container_port        = 8080
  health_check_path     = "/v1/health"
  cpu                   = "512"
  memory                = "1024"
  minimum_task_count    = 1
  maximum_task_count    = 1
  agentcore_runtime_arn = module.agentcore.runtime_arn
}

module "agentcore" {
  source = "./modules/agentcore"

  project_name = var.project_name
  aws_region   = var.aws_region
  image_uri    = "${data.aws_ecr_repository.mcp.repository_url}:${var.image_tag}"
}
