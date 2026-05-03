"""
Zamboni — Structured Logger
Outputs JSON to stdout — captured by CloudWatch Logs Agent on EC2.
Usage: log = get_logger(__name__)
"""
import logging

import structlog

from config.settings import LOG_LEVEL

_configured = False


def _configure():
    global _configured
    if _configured:
        return
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    _configure()
    return structlog.get_logger(name)
