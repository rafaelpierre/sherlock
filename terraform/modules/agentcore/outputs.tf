output "ecr_repository_url" {
  value = aws_ecr_repository.agent.repository_url
}

output "runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
}

output "runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_id
}

output "execution_role_arn" {
  value = aws_iam_role.agentcore_execution.arn
}
