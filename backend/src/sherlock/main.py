"""Command-line entry point for the Sherlock Text2SQL agent."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sherlock.config import Settings
from sherlock.services.text2sql import create_text2sql_service

DEFAULT_QUESTION = "How many transactions are fraudulent and non-fraudulent?"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask a natural-language question about the fraud database."
    )
    parser.add_argument(
        "question",
        nargs="*",
        help=f"question to answer (default: {DEFAULT_QUESTION!r})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    question = " ".join(args.question).strip() or DEFAULT_QUESTION
    service = create_text2sql_service(Settings.from_environment())
    try:
        result = asyncio.run(service.query(question))
        print(result)
    finally:
        service.close()


if __name__ == "__main__":
    main()
