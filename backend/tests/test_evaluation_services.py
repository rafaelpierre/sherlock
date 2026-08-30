import asyncio

import pytest

from sherlock.config import Settings
from sherlock.evaluation.models import EvaluationCase
from sherlock.evaluation.services import FixtureEvaluationService, LiveEvaluationService


def test_fixture_service_returns_committed_response_without_live_services() -> None:
    case = EvaluationCase(
        id="offline",
        prompt="offline",
        expected={"value": 1},
        fixture_response={"value": 1},
    )

    result = asyncio.run(FixtureEvaluationService().execute("text2sql", case))

    assert result == {"value": 1}


def test_fixture_service_requires_a_response() -> None:
    case = EvaluationCase(id="live-only", prompt="live", expected={"value": 1})

    with pytest.raises(ValueError, match="rerun with --live"):
        asyncio.run(FixtureEvaluationService().execute("rule_generation", case))


def test_live_service_passes_model_and_routes_workflows(monkeypatch) -> None:
    constructed: list[tuple[str, str | None]] = []

    class TextWorkflow:
        async def query(self, prompt: str) -> dict:
            return {"question": prompt}

        def close(self) -> None:
            constructed.append(("closed", "text2sql"))

    class RuleWorkflow:
        async def generate(self, prompt: str) -> dict:
            return {"rule": prompt}

        def close(self) -> None:
            constructed.append(("closed", "rules"))

    def text_factory(settings, *, model=None):
        constructed.append(("text2sql", model))
        return TextWorkflow()

    def rule_factory(settings, *, model=None):
        constructed.append(("rules", model))
        return RuleWorkflow()

    monkeypatch.setattr(
        "sherlock.evaluation.services.create_text2sql_service", text_factory
    )
    monkeypatch.setattr(
        "sherlock.evaluation.services.create_rule_generation_service", rule_factory
    )
    service = LiveEvaluationService(Settings(), model="bedrock-model-v1")
    case = EvaluationCase(id="live", prompt="live prompt", expected={"ok": True})

    text_result = asyncio.run(service.execute("text2sql", case))
    rule_result = asyncio.run(service.execute("rule_generation", case))
    service.close()

    assert text_result == {"question": "live prompt"}
    assert rule_result == {"rule": "live prompt"}
    assert constructed == [
        ("text2sql", "bedrock-model-v1"),
        ("rules", "bedrock-model-v1"),
        ("closed", "text2sql"),
        ("closed", "rules"),
    ]
