# Copyright (c) Data Agent Team. All rights reserved.
"""Logging configuration."""

import sys
from loguru import logger


def configure_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=log_format,
        level=level,
        colorize=True,
    )

    logger.add(
        "data-agent.log",
        format=log_format,
        level=level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
    )
