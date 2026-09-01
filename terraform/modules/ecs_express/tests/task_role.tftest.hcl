mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  project_name       = "sherlock"
  aws_region         = "eu-west-2"
  service_name       = "sherlock-backend"
  image_uri          = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/sherlock-backend:test"
  container_port     = 8080
  health_check_path  = "/v1/health"
  cpu                = "512"
  memory             = "1024"
  minimum_task_count = 1
  maximum_task_count = 1
}

run "backend_creates_agentcore_task_role" {
  command = plan

  variables {
    create_agentcore_task_role = true
    agentcore_runtime_arn      = "arn:aws:bedrock-agentcore:eu-west-2:123456789012:runtime/example"
  }

  assert {
    condition     = length(aws_iam_role.task) == 1
    error_message = "An AgentCore-enabled service must create an ECS task role."
  }

  assert {
    condition     = length(aws_iam_role_policy.backend_task) == 1
    error_message = "An AgentCore-enabled service must attach its invocation policy."
  }

  assert {
    condition     = aws_iam_role_policy_attachment.infrastructure.policy_arn == "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices"
    error_message = "An Express service infrastructure role must use the ECS service-role managed policy."
  }

  assert {
    condition     = aws_ecs_express_gateway_service.this.scaling_target[0].auto_scaling_metric == "AVERAGE_CPU"
    error_message = "Express service scaling must explicitly use the AWS CPU default."
  }

  assert {
    condition     = aws_ecs_express_gateway_service.this.scaling_target[0].auto_scaling_target_value == 60
    error_message = "Express service scaling must explicitly use the AWS target default."
  }
}

run "frontend_does_not_create_agentcore_task_role" {
  command = plan

  variables {
    create_agentcore_task_role = false
  }

  assert {
    condition     = length(aws_iam_role.task) == 0
    error_message = "A frontend service must not create an AgentCore task role."
  }

  assert {
    condition     = length(aws_iam_role_policy.backend_task) == 0
    error_message = "A frontend service must not attach the backend invocation policy."
  }
}
