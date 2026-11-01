"""Logging setup for the command line interface."""

from __future__ import annotations

import logging

LOGGER_NAME = "themeshift"


def configure(verbosity: str = "info") -> logging.Logger:
    """Configure and return the project logger.

    ``verbosity`` is one of ``quiet``, ``info`` or ``debug``.
    """

    level = {
        "quiet": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
    }.get(verbosity, logging.INFO)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    for handler in logger.handlers:
        handler.setLevel(level)
    return logger
