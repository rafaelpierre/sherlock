"""Test-only defaults for services that require explicit local configuration."""

from __future__ import annotations

import os

os.environ.setdefault("SHERLOCK_AUTH_REQUIRED", "false")
