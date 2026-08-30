import json

import pytest
from pydantic import ValidationError

from sherlock.evaluation.fixtures import FixtureError, load_suites, select_suites
from sherlock.evaluation.models import EvaluationSuite


def suite_payload(name: str = "example") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "A deterministic suite.",
        "kind": "text2sql",
        "cases": [
            {
                "id": "case-one",
                "prompt": "Count transactions",
                "expected": {"count": 3},
                "fixture_response": {"count": 3},
            }
        ],
    }


def test_load_and_select_versioned_suites(tmp_path) -> None:
    (tmp_path / "second.json").write_text(
        json.dumps(suite_payload("second")), encoding="utf-8"
    )
    (tmp_path / "first.json").write_text(
        json.dumps(suite_payload("first")), encoding="utf-8"
    )

    suites = load_suites(tmp_path)

    assert list(suites) == ["first", "second"]
    assert [suite.name for suite in select_suites(suites, ["second"])] == ["second"]
    assert [suite.name for suite in select_suites(suites, None)] == [
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    ("filename", "contents", "message"),
    [
        ("broken.json", "{", "Malformed JSON"),
        (
            "wrong-version.json",
            json.dumps({**suite_payload(), "schema_version": 2}),
            "Invalid fixture",
        ),
    ],
)
def test_load_suites_reports_invalid_files(
    tmp_path, filename: str, contents: str, message: str
) -> None:
    (tmp_path / filename).write_text(contents, encoding="utf-8")

    with pytest.raises(FixtureError, match=message):
        load_suites(tmp_path)


def test_load_suites_rejects_missing_empty_and_duplicate_suites(tmp_path) -> None:
    with pytest.raises(FixtureError, match="does not exist"):
        load_suites(tmp_path / "missing")
    with pytest.raises(FixtureError, match="No JSON suite fixtures"):
        load_suites(tmp_path)

    for filename in ("one.json", "two.json"):
        (tmp_path / filename).write_text(json.dumps(suite_payload()), encoding="utf-8")
    with pytest.raises(FixtureError, match="Duplicate suite name"):
        load_suites(tmp_path)


def test_select_suites_rejects_unknown_and_duplicate_selection() -> None:
    suite = EvaluationSuite.model_validate(suite_payload())
    available = {suite.name: suite}

    with pytest.raises(FixtureError, match="Unknown suite.*missing"):
        select_suites(available, ["missing"])
    with pytest.raises(FixtureError, match="more than once"):
        select_suites(available, ["example", "example"])


def test_suite_rejects_duplicate_case_ids() -> None:
    payload = suite_payload()
    payload["cases"].append(payload["cases"][0])

    with pytest.raises(ValidationError, match="case ids must be unique"):
        EvaluationSuite.model_validate(payload)
