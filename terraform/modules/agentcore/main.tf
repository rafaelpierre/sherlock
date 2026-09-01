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
      "arn:aws:ecr:${var.aws_region}:*:repository/${var.project_name}-mcp"
    ]
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
      container_uri = var.image_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "MCP"
  }
}
