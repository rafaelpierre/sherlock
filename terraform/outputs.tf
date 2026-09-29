output "agent_runtime_arn" {
  description = "ARN of the deployed AgentCore runtime"
  value       = module.agentcore.runtime_arn
}

output "agent_runtime_id" {
  description = "ID of the deployed AgentCore runtime"
  value       = module.agentcore.runtime_id
}

output "agent_execution_role_arn" {
  description = "IAM role used by the AgentCore runtime"
  value       = module.agentcore.execution_role_arn
}

output "frontend_url" {
  description = "Public HTTPS URL for the Sherlock investigation workspace"
  value       = module.frontend.endpoint
}

output "backend_url" {
  description = "Public HTTPS URL for the backend API PoC service"
  value       = module.backend.endpoint
}
