resource "terraform_data" "agentcore_runtime" {}

module "backend" {
  source = "../../../modules/ecs_express"

  project_name               = "sherlock"
  aws_region                 = "eu-west-2"
  service_name               = "sherlock-backend"
  image_uri                  = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/sherlock-backend:test"
  container_port             = 8080
  health_check_path          = "/v1/health"
  cpu                        = "512"
  memory                     = "1024"
  minimum_task_count         = 1
  maximum_task_count         = 1
  create_agentcore_task_role = true
  agentcore_runtime_arn      = terraform_data.agentcore_runtime.id
}
