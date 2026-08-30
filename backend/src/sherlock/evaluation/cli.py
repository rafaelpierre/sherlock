"""Command-line entry point for reproducible Sherlock evaluation runs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from sherlock.config import REPOSITORY_ROOT, Settings
from sherlock.evaluation.fixtures import FixtureError, load_suites, select_suites
from sherlock.evaluation.models import EvaluationReport, EvaluationSuite, RunMetadata
from sherlock.evaluation.runner import EvaluationRunner
from sherlock.evaluation.services import (
    EvaluationService,
    FixtureEvaluationService,
    LiveEvaluationService,
)

EXIT_SUCCESS = 0
EXIT_CASE_FAILURE = 1
EXIT_CONFIGURATION_ERROR = 2
DEFAULT_FIXTURES = REPOSITORY_ROOT / "evals" / "cases"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "eval-results" / "report.json"
DEFAULT_DATASET = REPOSITORY_ROOT / "mcp" / "db" / "data" / "data.db"


class ReportWriteError(RuntimeError):
    """The machine-readable report could not be serialized or persisted."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sherlock-eval",
        description="Run versioned Sherlock evaluation suites.",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES,
        help="directory containing versioned suite JSON files",
    )
    parser.add_argument(
        "--suite",
        action="append",
        help="suite name to run; repeat to select multiple (default: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="machine-readable report destination",
    )
    parser.add_argument(
        "--dataset-revision",
        help="dataset revision label (default: hash of bundled SQLite dataset)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly enable live MCP and Bedrock execution",
    )
    parser.add_argument(
        "--model",
        help="Strands model identifier; required with --live",
    )
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    service: EvaluationService | None = None,
) -> int:
    """Run the CLI and return its documented process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.live and not args.model:
        parser.error("--model is required with --live")

    try:
        suites = select_suites(load_suites(args.fixtures), args.suite)
        evaluation_service = service or _create_service(args.live, args.model)
        metadata = _metadata(args, suites, now=now)
        try:
            report = asyncio.run(
                EvaluationRunner(evaluation_service).run(suites, metadata)
            )
        finally:
            evaluation_service.close()
        _write_report(args.output, report)
    except (FixtureError, OSError, RuntimeError, ValueError) as exc:
        print(f"Evaluation configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION_ERROR

    _print_summary(report, args.output)
    if report.summary.failed or report.summary.errors:
        return EXIT_CASE_FAILURE
    return EXIT_SUCCESS


def _create_service(live: bool, model: str | None) -> EvaluationService:
    if not live:
        return FixtureEvaluationService()
    if model is None:
        raise ValueError("A model is required for live execution")
    return LiveEvaluationService(Settings.from_environment(), model=model)


def _metadata(
    args: argparse.Namespace,
    suites: list[EvaluationSuite],
    *,
    now: datetime | None,
) -> RunMetadata:
    mode = "live" if args.live else "fixture"
    configuration = {
        "comparison": "recursive-subset-v1",
        "fixture_directory": str(args.fixtures.resolve()),
        "mcp_transport": (
            Settings.from_environment().mcp_transport if args.live else "disabled"
        ),
        "runner_schema_version": 1,
    }
    return RunMetadata(
        started_at=now or datetime.now(UTC),
        model=args.model or "deterministic-fixture-v1",
        configuration=configuration,
        dataset_revision=args.dataset_revision or _bundled_dataset_revision(),
        mode=mode,
        selected_suites=[suite.name for suite in suites],
    )


def _bundled_dataset_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "hash-object", str(DEFAULT_DATASET)],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        revision = result.stdout.strip()
        if revision:
            return f"git-blob:{revision}"
    except (OSError, subprocess.SubprocessError):
        pass

    digest = hashlib.sha256()
    with DEFAULT_DATASET.open("rb") as dataset:
        while chunk := dataset.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _write_report(path: Path, report: EvaluationReport) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = report.model_dump_json(indent=2) + "\n"
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    except Exception as exc:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)
        raise ReportWriteError(f"Could not write report {path}: {exc}") from exc


def _print_summary(report: EvaluationReport, output: Path) -> None:
    summary = report.summary
    print(
        f"Suites: {', '.join(report.metadata.selected_suites)} | "
        f"Cases: {summary.total} | Passed: {summary.passed} | "
        f"Failed: {summary.failed} | Errors: {summary.errors}"
    )
    print(
        f"Latency: {summary.total_latency_ms:.3f} ms | "
        f"Repairs: {summary.repair_count} | Repair rate: {summary.repair_rate:.1%}"
    )
    print(f"Report: {output}")


def main() -> None:
    raise SystemExit(run_cli())
