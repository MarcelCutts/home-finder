"""Structured logging configuration."""

import logging
import sys

import structlog


def configure_logging(*, json_output: bool = False, level: int = logging.INFO) -> None:
    """Configure structlog for the application.

    Args:
        json_output: If True, output JSON logs (for production). Otherwise, pretty console output.
        level: Logging level (default: INFO).
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ExtraAdder(),
    ]

    if json_output:
        # Production: JSON output
        processors: list[structlog.types.Processor] = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: pretty console output
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a logger instance."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
