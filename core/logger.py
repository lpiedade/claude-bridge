"""Logging setup for the claude-bridge service."""
from __future__ import annotations

import logging


def configure() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


log = logging.getLogger("claude-bridge")
