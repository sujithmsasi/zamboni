"""
Zamboni — Notifier
SNS email dispatch for alerts, circuit breaker trips,
GREENZONE notifications, and PENDING_DROP warnings.
"""
import json
from datetime import date
from typing import Optional

import boto3

from config.settings import SNS_ALERT_TOPIC_ARN, SNS_GREENZONE_TOPIC_ARN, AWS_REGION
from engine.utils.logger import get_logger

log = get_logger(__name__)

_client: Optional[boto3.client] = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("sns", region_name=AWS_REGION)
    return _client


# ══════════════════════════════════════════════════════════════════════════════
#  CORE SEND
# ══════════════════════════════════════════════════════════════════════════════

def send(
    subject: str,
    message: str,
    topic_arn: str,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Publish a message to an SNS topic.
    Returns MessageId on success, None on dry_run.
    """
    if dry_run:
        log.info("notifier.dry_run", subject=subject, topic_arn=topic_arn)
        return None

    try:
        resp = _get_client().publish(
            TopicArn=topic_arn,
            Subject=subject[:100],   # SNS subject limit
            Message=message,
        )
        message_id = resp.get("MessageId")
        log.info("notifier.sent", subject=subject, message_id=message_id)
        return message_id
    except Exception as e:
        log.error("notifier.failed", subject=subject, error=str(e))
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  ALERT TYPES
# ══════════════════════════════════════════════════════════════════════════════

def send_alert(
    subject: str,
    message: str,
    table_fqn: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """General-purpose HK alert."""
    if table_fqn:
        message = f"Table: {table_fqn}\n\n{message}"
    return send(
        subject=f"[Zamboni Alert] {subject}",
        message=message,
        topic_arn=SNS_ALERT_TOPIC_ARN,
        dry_run=dry_run,
    )


def send_circuit_breaker_alert(
    table_fqn: str,
    failure_count: int,
    threshold: int,
    dry_run: bool = False,
) -> Optional[str]:
    """Alert when a table's circuit breaker trips and HK is auto-disabled."""
    subject = f"Circuit Breaker Tripped — {table_fqn}"
    message = (
        f"Zamboni has automatically disabled housekeeping for:\n"
        f"  Table    : {table_fqn}\n"
        f"  Failures : {failure_count} (threshold: {threshold})\n\n"
        f"Action required:\n"
        f"  1. Investigate the failures in the Zamboni Execution Log\n"
        f"  2. Fix the root cause\n"
        f"  3. Re-enable via Zamboni Streamlit app or CLI:\n"
        f"     python -m engine.cli.enable --table {table_fqn}\n"
    )
    return send_alert(subject, message, table_fqn=table_fqn, dry_run=dry_run)


def send_greenzone_notification(
    table_fqn: str,
    owner_email: str,
    expires_at: date,
    environment: str,
    days_inactive: int,
    dry_run: bool = False,
) -> Optional[str]:
    """
    GREENZONE notification — sent to table owner before auto-deletion.
    Owner has until expires_at to respond or the table will be PENDING_DROP.
    """
    subject = f"[Zamboni GREENZONE] Table marked for deletion — {table_fqn}"
    message = (
        f"This is an automated notification from Zamboni.\n\n"
        f"The following non-production table has been inactive for {days_inactive} days "
        f"and is now in the GREENZONE (scheduled for deletion):\n\n"
        f"  Table       : {table_fqn}\n"
        f"  Environment : {environment}\n"
        f"  Inactive    : {days_inactive} days\n"
        f"  Expires     : {expires_at.isoformat()}\n\n"
        f"If you still need this table, log in to the Zamboni app and submit an exemption "
        f"before {expires_at.isoformat()}.\n\n"
        f"If no action is taken, the table and its data will be permanently deleted.\n"
    )
    return send(
        subject=subject,
        message=message,
        topic_arn=SNS_GREENZONE_TOPIC_ARN,
        dry_run=dry_run,
    )


def send_pending_drop_notification(
    table_fqn: str,
    owner_email: str,
    drop_at: date,
    environment: str,
    dry_run: bool = False,
) -> Optional[str]:
    """48-hour final warning before table is permanently deleted."""
    subject = f"[Zamboni PENDING DROP] Final notice — {table_fqn}"
    message = (
        f"FINAL NOTICE — This table will be permanently deleted on {drop_at.isoformat()}.\n\n"
        f"  Table       : {table_fqn}\n"
        f"  Environment : {environment}\n"
        f"  Deletes at  : {drop_at.isoformat()}\n\n"
        f"To cancel deletion, log in to the Zamboni app immediately and submit an exemption.\n"
    )
    return send(
        subject=subject,
        message=message,
        topic_arn=SNS_GREENZONE_TOPIC_ARN,
        dry_run=dry_run,
    )


def send_engine_failure_summary(
    engine: str,
    run_id: str,
    failed_tables: list[str],
    dry_run: bool = False,
) -> Optional[str]:
    """Send a summary alert when an engine run has failures."""
    count   = len(failed_tables)
    subject = f"[Zamboni] {engine.upper()} Engine — {count} failure(s)"
    table_list = "\n".join(f"  - {t}" for t in failed_tables[:20])
    more = f"\n  ... and {count - 20} more" if count > 20 else ""
    message = (
        f"Zamboni {engine.upper()} Engine completed with failures.\n\n"
        f"  Run ID     : {run_id}\n"
        f"  Failed     : {count} table(s)\n\n"
        f"Failed tables:\n{table_list}{more}\n\n"
        f"Check the Zamboni Execution Log for details.\n"
    )
    return send_alert(subject, message, dry_run=dry_run)
