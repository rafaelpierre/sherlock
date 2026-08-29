# ------------------------------------------------------------
# ECR repository
# ------------------------------------------------------------

resource "aws_ecr_repository" "agent" {
  name                 = "${var.project_name}-agent"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ------------------------------------------------------------
# IAM role assumed by AgentCore
# ------------------------------------------------------------

data "aws_iam_policy_document" "agentcore_assume_role" {
  statement {
    effect = "Allow"

    actions = [
      "sts:AssumeRole"
    ]

    principals {
      type = "Service"

      identifiers = [
        "bedrock-agentcore.amazonaws.com"
      ]
    }
  }
}

resource "aws_iam_role" "agentcore_execution" {
  name = "${var.project_name}-agentcore-execution"

  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

# ------------------------------------------------------------
# Permissions used by the AgentCore runtime
# ------------------------------------------------------------

data "aws_iam_policy_document" "agentcore_permissions" {

  # AgentCore needs to pull its container
  statement {
    effect = "Allow"

    actions = [
      "ecr:GetAuthorizationToken"
    ]

    resources = ["*"]
  }

  statement {
    effect = "Allow"

    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer"
    ]

    resources = [
      aws_ecr_repository.agent.arn
    ]
  }

  # Our agent can invoke Bedrock
  statement {
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream"
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "agentcore_permissions" {
  name = "${var.project_name}-agentcore-permissions"
  role = aws_iam_role.agentcore_execution.id

  policy = data.aws_iam_policy_document.agentcore_permissions.json
}

# ------------------------------------------------------------
# AgentCore Runtime
# ------------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "agent" {
  agent_runtime_name = "${var.project_name}_agent"

  role_arn = aws_iam_role.agentcore_execution.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agent.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    AWS_REGION       = var.aws_region
    BEDROCK_MODEL_ID = var.bedrock_model_id
  }
}
