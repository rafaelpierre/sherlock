from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from sherlock.services.rule_generation import (
    InvalidCurrentRule,
    RuleGenerationService,
    StrandsRuleGenerator,
)
from sherlock.services.rule_validation import (
    RuleValidationError,
    RuleValidationResult,
)


class StubGenerator:
    def __init__(self, rules: list[str]) -> None:
        self.rules: Iterator[str] = iter(rules)
        self.repairs: list[tuple[str, str]] = []
        self.refinement_repairs: list[tuple[str, str, str]] = []

    async def generate(self, instruction: str) -> str:
        return next(self.rules)

    async def repair(
        self,
        instruction: str,
        previous_rule: str,
        validation: RuleValidationResult,
    ) -> str:
        self.repairs.append((instruction, previous_rule))
        return next(self.rules)

    async def refine(self, rule: str, instruction: str) -> str:
        return next(self.rules)

    async def repair_refinement(
        self,
        current_rule: str,
        instruction: str,
        previous_rule: str,
        validation: RuleValidationResult,
    ) -> str:
        self.refinement_repairs.append((current_rule, instruction, previous_rule))
        return next(self.rules)


class StubValidator:
    async def validate(self, rule: str) -> RuleValidationResult:
        if "missing" in rule:
            return RuleValidationResult(
                valid=False,
                rule=None,
                errors=[
                    RuleValidationError(
                        "UNKNOWN_COLUMN", "Column 'missing' does not exist."
                    )
                ],
            )
        return RuleValidationResult(valid=True, rule=rule.upper(), errors=[])


def test_valid_rule_is_returned_without_repair() -> None:
    service = RuleGenerationService(
        StubGenerator(["amount_usd > 1000"]),
        StubValidator(),
    )

    result = asyncio.run(service.generate("transactions above $1,000"))

    assert result == {
        "rule": "AMOUNT_USD > 1000",
        "valid": True,
        "repair_count": 0,
        "errors": [],
    }


def test_invalid_rule_is_repaired_within_bound() -> None:
    generator = StubGenerator(["missing > 1", "amount_usd > 1"])
    service = RuleGenerationService(
        generator,
        StubValidator(),
    )

    result = asyncio.run(service.generate("high amount"))

    assert result["valid"] is True
    assert result["repair_count"] == 1
    assert generator.repairs == [("high amount", "missing > 1")]


def test_repair_stops_at_configured_limit() -> None:
    generator = StubGenerator(["missing > 1", "missing > 2", "missing > 3"])
    service = RuleGenerationService(
        generator,
        StubValidator(),
        max_repair_attempts=2,
    )

    result = asyncio.run(service.generate("high amount"))

    assert result["valid"] is False
    assert result["rule"] is None
    assert result["repair_count"] == 2
    assert result["errors"][0]["code"] == "UNKNOWN_COLUMN"


def test_negative_repair_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        RuleGenerationService(
            StubGenerator([]),
            StubValidator(),
            max_repair_attempts=-1,
        )


def test_lifecycle_callbacks_run_once() -> None:
    starts = 0
    closes = 0

    async def start() -> None:
        nonlocal starts
        starts += 1

    def close() -> None:
        nonlocal closes
        closes += 1

    service = RuleGenerationService(
        StubGenerator(["amount_usd > 1", "amount_usd > 2"]),
        StubValidator(),
        start_callback=start,
        close_callback=close,
    )

    asyncio.run(service.generate("one"))
    asyncio.run(service.generate("two"))
    service.close()

    assert starts == 1
    assert closes == 1


def test_refinement_preserves_previous_rule() -> None:
    generator = StubGenerator(["amount_usd > 1500 AND card_type = 'Debit'"])
    service = RuleGenerationService(
        generator,
        StubValidator(),
    )

    result = asyncio.run(
        service.refine(
            "amount_usd > 1000 AND card_type = 'Debit'",
            "raise the threshold to $1,500",
        )
    )

    assert result["previous_rule"] == "AMOUNT_USD > 1000 AND CARD_TYPE = 'DEBIT'"
    assert result["rule"] == "AMOUNT_USD > 1500 AND CARD_TYPE = 'DEBIT'"
    assert result["repair_count"] == 0


def test_refinement_repairs_invalid_candidate() -> None:
    generator = StubGenerator(
        ["missing > 1", "missing > 2", "amount_usd > 1500 AND card_type = 'Debit'"]
    )
    service = RuleGenerationService(
        generator,
        StubValidator(),
    )

    result = asyncio.run(
        service.refine("amount_usd > 1000 AND card_type = 'Debit'", "raise it")
    )

    assert result["valid"] is True
    assert result["repair_count"] == 2
    assert generator.refinement_repairs == [
        (
            "AMOUNT_USD > 1000 AND CARD_TYPE = 'DEBIT'",
            "raise it",
            "missing > 1",
        ),
        (
            "AMOUNT_USD > 1000 AND CARD_TYPE = 'DEBIT'",
            "raise it",
            "missing > 2",
        ),
    ]
    assert generator.repairs == []


def test_refinement_repair_prompt_includes_complete_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    generator = StrandsRuleGenerator(Mock())
    monkeypatch.setattr(
        generator,
        "_invoke",
        lambda prompt: prompts.append(prompt) or "amount_usd > 1500",
    )
    validation = RuleValidationResult(
        valid=False,
        rule=None,
        errors=[RuleValidationError("UNKNOWN_COLUMN", "Unknown column.")],
    )

    result = asyncio.run(
        generator.repair_refinement(
            "amount_usd > 1000 AND card_type = 'Debit'",
            "raise it",
            "missing > 1",
            validation,
        )
    )

    assert result == "amount_usd > 1500"
    assert len(prompts) == 1
    assert (
        "Current candidate rule:\namount_usd > 1000 AND card_type = 'Debit'"
        in prompts[0]
    )
    assert "Refinement instruction:\nraise it" in prompts[0]
    assert "Invalid refined rule:\nmissing > 1" in prompts[0]
    assert '"code": "UNKNOWN_COLUMN"' in prompts[0]


def test_refinement_rejects_invalid_current_rule() -> None:
    service = RuleGenerationService(
        StubGenerator([]),
        StubValidator(),
    )

    with pytest.raises(InvalidCurrentRule) as caught:
        asyncio.run(service.refine("missing > 1", "raise it"))

    assert caught.value.validation.errors[0].code == "UNKNOWN_COLUMN"
