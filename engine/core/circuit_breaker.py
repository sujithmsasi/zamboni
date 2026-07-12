"""
Zamboni — Circuit Breaker
Tracks consecutive failures per table.
After CIRCUIT_BREAKER_THRESHOLD failures, disables the table in the registry.
Prevents a broken table from blocking every HK run indefinitely.
"""
from config.settings import CIRCUIT_BREAKER_THRESHOLD
from engine.utils.logger import get_logger

log = get_logger(__name__)

# States
CLOSED = "CLOSED"   # Normal — proceed with HK
OPEN   = "OPEN"     # Too many failures — skip this table


def check(table_fqn: str, days: int = 30) -> str:
    """
    Check if the circuit breaker is open for a table.

    Queries the execution_log for recent failures.
    Returns CLOSED (run normally) or OPEN (skip — too many failures).

    Imports execution_log lazily to avoid circular imports.
    """
    # Lazy import — execution_log imports from config, circuit_breaker is used by engines
    from engine.core.execution_log import get_failure_count

    failure_count = get_failure_count(table_fqn, days=days)

    if failure_count >= CIRCUIT_BREAKER_THRESHOLD:
        log.warning(
            "circuit_breaker.open",
            table_fqn=table_fqn,
            failure_count=failure_count,
            threshold=CIRCUIT_BREAKER_THRESHOLD,
        )
        return OPEN

    log.debug(
        "circuit_breaker.closed",
        table_fqn=table_fqn,
        failure_count=failure_count,
    )
    return CLOSED


def trip(table_fqn: str, failure_count: int, dry_run: bool = False) -> None:
    """
    Trip the circuit breaker — disable HK for this table in the registry
    and send an alert. Called by the HK Engine after recording a failure.

    Args:
        table_fqn:     Table to disable
        failure_count: How many failures triggered this trip
        dry_run:       If True, log only — don't disable or alert
    """
    # Lazy imports
    from engine.core import notifier, registry

    reason = (
        f"Circuit breaker tripped after {failure_count} consecutive failures "
        f"(threshold: {CIRCUIT_BREAKER_THRESHOLD}). "
        f"Table disabled — investigate and re-enable manually via Zamboni CLI or the app."
    )

    log.error(
        "circuit_breaker.tripped",
        table_fqn=table_fqn,
        failure_count=failure_count,
        dry_run=dry_run,
    )

    if not dry_run:
        registry.disable_hk(table_fqn, reason=reason)
        notifier.send_circuit_breaker_alert(
            table_fqn=table_fqn,
            failure_count=failure_count,
            threshold=CIRCUIT_BREAKER_THRESHOLD,
        )


def should_trip(failure_count: int) -> bool:
    """
    Pure logic check — should the circuit breaker trip given this failure count?
    Used in tests and by the HK Engine to decide whether to call trip().
    """
    return failure_count >= CIRCUIT_BREAKER_THRESHOLD
