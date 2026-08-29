terraform {
  required_version = ">= 1.10.0"

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

module "agentcore" {
  source = "./modules/agentcore"

  project_name     = var.project_name
  aws_region       = var.aws_region
  bedrock_model_id = var.bedrock_model_id

  # Passed dynamically by CI, usually github.sha
  image_tag = var.image_tag
}
