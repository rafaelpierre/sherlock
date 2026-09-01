mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

run "agentcore_runtime_does_not_make_task_role_count_unknown" {
  command = plan

  module {
    source = "./tests/fixtures/unknown_agentcore_runtime"
  }
}
