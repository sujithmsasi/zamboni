"""
Regression tests for engine/engines/lifecycle_engine.py's notify-before-
transition ordering (2026-07-10 audit fix).

Real gap found: _evaluate_table() wrote the GREENZONE/PENDING_DROP state
transition (and started the deletion countdown) BEFORE attempting the SNS
notification, and notifier.send() swallows failures, returning None with
no retry. A transient SNS failure meant an owner's final warning could
silently never arrive while the countdown to a real Glue DROP still ran.
Fixed by notifying first and only transitioning on success -- a failure
leaves the table in its current state so the next scheduled scan retries.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import engine.engines.lifecycle_engine as lifecycle_engine_mod
from engine.engines.lifecycle_engine import GREENZONE, PENDING_DROP, STALE_CANDIDATE, LifecycleEngine


def _stale_candidate_row(fqn="glue_catalog.finance_preprod_db.t") -> dict:
    return {
        "table_fqn": fqn,
        "lifecycle_state": STALE_CANDIDATE,
        "days_since_activity": 90,
        "stale_threshold_days": 60,
        "owner_exempted": False,
        "owner_email": "owner@company.com",
        "environment": "preprod",
    }


def _greenzone_row(fqn="glue_catalog.finance_preprod_db.t") -> dict:
    return {
        "table_fqn": fqn,
        "lifecycle_state": GREENZONE,
        "owner_exempted": False,
        "owner_email": "owner@company.com",
        "environment": "preprod",
        "greenzone_expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  STALE_CANDIDATE → GREENZONE
# ══════════════════════════════════════════════════════════════════════════════

def test_greenzone_notify_failure_defers_transition_and_alerts(monkeypatch):
    engine = LifecycleEngine(dry_run=False)

    monkeypatch.setattr(lifecycle_engine_mod.notifier, "send_greenzone_notification", lambda **k: None)
    alerts = []
    monkeypatch.setattr(lifecycle_engine_mod.notifier, "send_alert", lambda **k: alerts.append(k))

    transition_calls = []
    monkeypatch.setattr(engine, "_transition", lambda *a, **k: transition_calls.append((a, k)))
    monkeypatch.setattr(engine, "_write_log", lambda *a, **k: None)

    outcome = engine._evaluate_table(_stale_candidate_row())

    assert outcome == "notify_failed"
    assert transition_calls == [], "must NOT advance state when the notification failed"
    assert len(alerts) == 1
    assert "GREENZONE" in alerts[0]["subject"]


def test_greenzone_notify_success_transitions(monkeypatch):
    engine = LifecycleEngine(dry_run=False)

    monkeypatch.setattr(lifecycle_engine_mod.notifier, "send_greenzone_notification", lambda **k: "msg-123")
    monkeypatch.setattr(engine, "_write_log", lambda *a, **k: None)

    transition_calls = []
    monkeypatch.setattr(engine, "_transition", lambda *a, **k: transition_calls.append((a, k)))

    outcome = engine._evaluate_table(_stale_candidate_row())

    assert outcome == "notified"
    assert len(transition_calls) == 1
    args, kwargs = transition_calls[0]
    assert args[1:3] == (STALE_CANDIDATE, GREENZONE)
    assert "greenzone_expires_at" in kwargs


def test_greenzone_dry_run_never_calls_notifier(monkeypatch):
    """dry_run mode must not attempt a real SNS send -- matches the
    pre-existing behavior this fix must not change."""
    engine = LifecycleEngine(dry_run=True)

    notify_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod.notifier, "send_greenzone_notification",
        lambda **k: notify_calls.append(k) or "msg-1",
    )
    monkeypatch.setattr(engine, "_write_log", lambda *a, **k: None)
    transition_calls = []
    monkeypatch.setattr(engine, "_transition", lambda *a, **k: transition_calls.append((a, k)))

    outcome = engine._evaluate_table(_stale_candidate_row())

    assert outcome == "notified"
    assert notify_calls == []
    assert len(transition_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
#  GREENZONE → PENDING_DROP
# ══════════════════════════════════════════════════════════════════════════════

def test_pending_drop_notify_failure_defers_transition_and_alerts(monkeypatch):
    engine = LifecycleEngine(dry_run=False)

    monkeypatch.setattr(lifecycle_engine_mod.notifier, "send_pending_drop_notification", lambda **k: None)
    alerts = []
    monkeypatch.setattr(lifecycle_engine_mod.notifier, "send_alert", lambda **k: alerts.append(k))

    transition_calls = []
    monkeypatch.setattr(engine, "_transition", lambda *a, **k: transition_calls.append((a, k)))
    monkeypatch.setattr(engine, "_write_log", lambda *a, **k: None)

    outcome = engine._evaluate_table(_greenzone_row())

    assert outcome == "notify_failed"
    assert transition_calls == [], "must NOT start the PENDING_DROP countdown when the final notice failed to send"
    assert len(alerts) == 1
    assert "PENDING DROP" in alerts[0]["subject"]


def test_pending_drop_notify_success_transitions(monkeypatch):
    engine = LifecycleEngine(dry_run=False)

    monkeypatch.setattr(lifecycle_engine_mod.notifier, "send_pending_drop_notification", lambda **k: "msg-456")
    monkeypatch.setattr(engine, "_write_log", lambda *a, **k: None)

    transition_calls = []
    monkeypatch.setattr(engine, "_transition", lambda *a, **k: transition_calls.append((a, k)))

    outcome = engine._evaluate_table(_greenzone_row())

    assert outcome == "notified"
    assert len(transition_calls) == 1
    args, kwargs = transition_calls[0]
    assert args[1:3] == (GREENZONE, PENDING_DROP)
    assert "pending_drop_expires_at" in kwargs


# ══════════════════════════════════════════════════════════════════════════════
#  run() tallies notify_failed separately from succeeded/skipped
# ══════════════════════════════════════════════════════════════════════════════

def test_run_tallies_notify_failed_separately(monkeypatch):
    engine = LifecycleEngine(dry_run=False)

    monkeypatch.setattr(engine, "_get_active_registry_tables", lambda env: [_stale_candidate_row()])
    monkeypatch.setattr(engine, "_evaluate_table", lambda row: "notify_failed")

    result = engine.run(environment="preprod")

    assert result["notify_failed"] == 1
    assert result["succeeded"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 0
