data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ecs_infrastructure_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.service_name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "infrastructure" {
  name               = "${var.service_name}-infrastructure"
  assume_role_policy = data.aws_iam_policy_document.ecs_infrastructure_assume_role.json
}

resource "aws_iam_role_policy_attachment" "infrastructure" {
  role       = aws_iam_role.infrastructure.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices"
}

resource "aws_iam_role" "task" {
  count              = var.create_agentcore_task_role ? 1 : 0
  name               = "${var.service_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

data "aws_iam_policy_document" "backend_task" {
  count = var.create_agentcore_task_role ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock-agentcore:InvokeAgentRuntime",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "backend_task" {
  count  = var.create_agentcore_task_role ? 1 : 0
  name   = "${var.service_name}-runtime"
  role   = aws_iam_role.task[0].id
  policy = data.aws_iam_policy_document.backend_task[0].json
}

locals {
  environment = concat(
    var.backend_url == null ? [] : [{ name = "BACKEND_URL", value = var.backend_url }],
    var.create_agentcore_task_role ? [
      { name = "AWS_REGION", value = var.aws_region },
      { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      { name = "SHERLOCK_MCP_TRANSPORT", value = "agentcore" },
      { name = "SHERLOCK_AGENTCORE_RUNTIME_ARN", value = var.agentcore_runtime_arn },
    ] : [],
  )
}

resource "aws_ecs_express_gateway_service" "this" {
  service_name            = var.service_name
  execution_role_arn      = aws_iam_role.execution.arn
  infrastructure_role_arn = aws_iam_role.infrastructure.arn
  task_role_arn           = var.create_agentcore_task_role ? aws_iam_role.task[0].arn : null
  cpu                     = var.cpu
  memory                  = var.memory
  health_check_path       = var.health_check_path
  wait_for_steady_state   = true

  primary_container {
    image          = var.image_uri
    container_port = var.container_port

    dynamic "environment" {
      for_each = local.environment
      content {
        name  = environment.value.name
        value = environment.value.value
      }
    }
  }

  scaling_target {
    min_task_count = var.minimum_task_count
    max_task_count = var.maximum_task_count
  }

  depends_on = [
    aws_iam_role_policy_attachment.execution,
    aws_iam_role_policy_attachment.infrastructure,
    aws_iam_role_policy.backend_task,
  ]
}
