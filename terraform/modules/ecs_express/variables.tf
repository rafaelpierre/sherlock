variable "project_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "service_name" {
  type = string
}

variable "image_uri" {
  type = string
}

variable "container_port" {
  type = number
}

variable "health_check_path" {
  type = string
}

variable "cpu" {
  type = string
}

variable "memory" {
  type = string
}

variable "minimum_task_count" {
  type = number
}

variable "maximum_task_count" {
  type = number
}

variable "backend_url" {
  type     = string
  default  = null
  nullable = true
}

variable "agentcore_runtime_arn" {
  type     = string
  default  = null
  nullable = true
}

variable "runtime_secret_arns" {
  type    = list(string)
  default = []
}

variable "phoenix_secret_id" {
  type     = string
  default  = null
  nullable = true
}
