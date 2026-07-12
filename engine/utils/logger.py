"""
Zamboni — Structured Logger
Outputs JSON to stdout/stderr. On EC2 each entry point redirects that to a
file under /var/log/zamboni/ (systemd's `append:` for the daemons, plain
`>> ... 2>&1` shell redirection for the EventBridge-triggered engine
scripts) -- the CloudWatch Agent tails those files and /etc/logrotate.d/
zamboni rotates them weekly, both configured by
deploy/scripts/configure_logging.sh (called from after_install.sh on
every deploy, not EC2 UserData -- UserData only runs once, at first boot).
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
