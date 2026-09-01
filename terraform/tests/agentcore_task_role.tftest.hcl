mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  project_name       = "sherlock"
  frontend_image_tag = "0000000000000000000000000000000000000000"
  backend_image_tag  = "0000000000000000000000000000000000000000"
  mcp_image_tag      = "0000000000000000000000000000000000000000"
}

run "agentcore_runtime_does_not_make_task_role_count_unknown" {
  command = plan
}
