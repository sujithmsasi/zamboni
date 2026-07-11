"""
Unit tests for engine/core/lock_service.py::LockHeartbeat.

Real gap found in a 2026-07-10 audit: LOCK_HEARTBEAT_SECONDS and
LockService.heartbeat() both existed (contracts.md §3.1) but nothing ever
called heartbeat() -- a genuinely long-running operation could outlive its
lock's TTL and let a second trigger acquire the "released" lock while the
first run was still in flight. LockHeartbeat is the fix: a background
ticker wired into orchestrator.py's run_table_maintenance(),
archival_engine.py's _process_table(), lifecycle_engine.py's
run_cleanup(), and hk_engine.py's legacy per-op path.

2026-07-11 audit fix: ownership is a LEASE, not a fire-and-forget
background log line -- LockHeartbeat now tracks `lost`/`assert_held()` so
callers can tell when the lease was rejected (stolen) or failed too many
times in a row to still trust it, and refuse to start new destructive
work once that happens.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from engine.core.lock_service import Lock, LockHeartbeat, LockLostError


def _lock() -> Lock:
    now = datetime.now(UTC)
    return Lock(
        table_fqn="glue_catalog.db.t", lock_owner="me", operation="test",
        acquired_at=now, heartbeat_at=now, expires_at=now,
    )


def test_heartbeat_calls_lock_service_heartbeat_periodically():
    lock_service = MagicMock()
    lock = _lock()
    hb = LockHeartbeat(lock_service, lock, interval_s=0.05)
    hb.start()
    try:
        time.sleep(0.22)
    finally:
        hb.stop()

    assert lock_service.heartbeat.call_count >= 2
    lock_service.heartbeat.assert_called_with(lock)


def test_heartbeat_stop_is_prompt_not_blocked_on_full_interval():
    """stop() must not sit idle for the full interval before returning --
    it should unblock the ticker's wait() immediately."""
    lock_service = MagicMock()
    lock = _lock()
    hb = LockHeartbeat(lock_service, lock, interval_s=60)
    hb.start()
    started = time.monotonic()
    hb.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 2, "stop() must not block for the full heartbeat interval"
    assert lock_service.heartbeat.call_count == 0, "60s interval must not have ticked yet"


def test_heartbeat_transient_failure_does_not_crash_thread_or_mark_lost():
    """A single transient DynamoDB/SQLite error must not kill the ticker
    NOR immediately declare the lease lost -- only MAX_CONSECUTIVE_FAILURES
    in a row does that (see test below). One bad tick, recovered on the
    next, must leave `lost` False."""
    lock_service = MagicMock()
    lock_service.heartbeat.side_effect = [RuntimeError("boom"), True, True]
    lock = _lock()
    hb = LockHeartbeat(lock_service, lock, interval_s=0.05)
    hb.start()
    try:
        time.sleep(0.22)
    finally:
        hb.stop()

    assert lock_service.heartbeat.call_count >= 1
    assert hb.lost is False


def test_heartbeat_permanent_failure_marks_lease_lost_after_threshold():
    """MAX_CONSECUTIVE_FAILURES heartbeat *errors* in a row (not
    rejections) must permanently mark the lease lost -- past that point
    we can no longer trust the lease is still being renewed."""
    lock_service = MagicMock()
    lock_service.heartbeat.side_effect = RuntimeError("DynamoDB unreachable")
    lock = _lock()
    hb = LockHeartbeat(lock_service, lock, interval_s=0.03)
    hb.MAX_CONSECUTIVE_FAILURES = 2
    hb.start()
    try:
        time.sleep(0.2)
    finally:
        hb.stop()

    assert hb.lost is True
    with pytest.raises(LockLostError):
        hb.assert_held()


def test_heartbeat_rejected_marks_lease_lost_immediately():
    """A rejected heartbeat (lock_owner no longer matches -- the lease was
    genuinely stolen after TTL expiry) must mark the lease lost on the
    very first rejection, not require repeated failures like a transient
    error does."""
    lock_service = MagicMock()
    lock_service.heartbeat.return_value = False
    lock = _lock()
    hb = LockHeartbeat(lock_service, lock, interval_s=0.03)
    hb.start()
    try:
        time.sleep(0.08)
    finally:
        hb.stop()

    assert hb.lost is True
    with pytest.raises(LockLostError, match="lease"):
        hb.assert_held()


def test_lease_lost_is_permanent_even_after_a_later_successful_tick():
    """Once lost (lock theft), a later successful heartbeat must not
    un-set it -- the lease is gone for good, this process is no longer
    the authoritative owner even if it could still reach the backend."""
    lock_service = MagicMock()
    lock_service.heartbeat.side_effect = [False, True, True, True]
    lock = _lock()
    hb = LockHeartbeat(lock_service, lock, interval_s=0.03)
    hb.start()
    try:
        time.sleep(0.15)
    finally:
        hb.stop()

    assert hb.lost is True


def test_assert_held_does_not_raise_when_lease_never_lost():
    lock_service = MagicMock()
    lock_service.heartbeat.return_value = True
    hb = LockHeartbeat(lock_service, _lock(), interval_s=60)
    hb.assert_held()  # must not raise -- never started, never lost
    assert hb.lost is False


def test_context_manager_starts_and_stops():
    lock_service = MagicMock()
    lock = _lock()
    with LockHeartbeat(lock_service, lock, interval_s=0.03) as hb:
        assert hb._thread.is_alive()
        time.sleep(0.08)
    assert not hb._thread.is_alive()
    assert lock_service.heartbeat.call_count >= 1
