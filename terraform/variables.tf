variable "project_name" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "eu-west-2"
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
}
