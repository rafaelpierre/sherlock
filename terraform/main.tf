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

data "aws_caller_identity" "current" {}

locals {
  cognito_enabled = length(var.cognito_callback_urls) > 0
}

resource "aws_cognito_user_pool" "sherlock" {
  count = local.cognito_enabled ? 1 : 0

  name = "${var.project_name}-users"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 14
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }
}

resource "aws_cognito_user_pool_client" "frontend" {
  count = local.cognito_enabled ? 1 : 0

  name                                 = "${var.project_name}-frontend"
  user_pool_id                         = aws_cognito_user_pool.sherlock[0].id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid"]
  callback_urls                        = var.cognito_callback_urls
  logout_urls                          = var.cognito_callback_urls
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
}

resource "aws_cognito_user_pool_domain" "sherlock" {
  count = local.cognito_enabled ? 1 : 0

  domain       = "${var.project_name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.sherlock[0].id
}

module "frontend" {
  source = "./modules/ecs_express"

  project_name             = var.project_name
  aws_region               = var.aws_region
  service_name             = "${var.project_name}-frontend"
  image_uri                = "${data.aws_ecr_repository.frontend.repository_url}:${var.frontend_image_tag}"
  container_port           = 80
  health_check_path        = "/health"
  cpu                      = "256"
  memory                   = "512"
  minimum_task_count       = 1
  maximum_task_count       = 1
  backend_url              = module.backend.endpoint
  cognito_issuer           = local.cognito_enabled ? "https://${aws_cognito_user_pool.sherlock[0].endpoint}" : null
  cognito_client_id        = local.cognito_enabled ? aws_cognito_user_pool_client.frontend[0].id : null
  cognito_hosted_ui_domain = local.cognito_enabled ? "https://${aws_cognito_user_pool_domain.sherlock[0].domain}.auth.${var.aws_region}.amazoncognito.com" : null
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
  cognito_issuer             = local.cognito_enabled ? "https://${aws_cognito_user_pool.sherlock[0].endpoint}" : null
  cognito_client_id          = local.cognito_enabled ? aws_cognito_user_pool_client.frontend[0].id : null
}

module "agentcore" {
  source = "./modules/agentcore"

  project_name = var.project_name
  aws_region   = var.aws_region
  image_uri    = "${data.aws_ecr_repository.mcp.repository_url}:${var.mcp_image_tag}"
}
