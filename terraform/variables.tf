variable "project_name" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "eu-west-2"
}

variable "frontend_image_tag" {
  description = "Immutable frontend multi-architecture image tag to deploy"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.frontend_image_tag))
    error_message = "frontend_image_tag must be a lowercase 40-character commit SHA."
  }
}

variable "backend_image_tag" {
  description = "Immutable backend multi-architecture image tag to deploy"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.backend_image_tag))
    error_message = "backend_image_tag must be a lowercase 40-character commit SHA."
  }
}

variable "mcp_image_tag" {
  description = "Immutable MCP multi-architecture image tag to deploy"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.mcp_image_tag))
    error_message = "mcp_image_tag must be a lowercase 40-character commit SHA."
  }
}

variable "cognito_callback_urls" {
  description = "Exact HTTPS URLs Cognito may redirect to after browser login."
  type        = list(string)

  validation {
    condition     = length(var.cognito_callback_urls) > 0 && alltrue([for url in var.cognito_callback_urls : can(regex("^https://", url))])
    error_message = "cognito_callback_urls must contain one or more HTTPS URLs."
  }
}
