output "endpoint" {
  description = "Managed HTTPS ingress endpoint"
  value       = aws_ecs_express_gateway_service.this.ingress_paths[0].endpoint
}
