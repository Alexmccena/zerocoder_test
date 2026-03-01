from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import BoundLogger

from trading_bot.config.schema import ObservabilityConfig, RuntimeConfig


def configure_logging(observability: ObservabilityConfig, runtime: RuntimeConfig) -> BoundLogger:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, observability.log_level.upper(), logging.INFO),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger("trading_bot").bind(
        service=runtime.service_name,
        environment=runtime.environment.value,
        run_mode=runtime.mode.value,
    )


def shutdown_logging() -> None:
    logging.shutdown()
