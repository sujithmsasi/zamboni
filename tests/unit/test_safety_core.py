"""
Unit tests for Phase 1a Safety Core:
  - engine/core/lock_service.py     (SQLite + DynamoDB/Stubber backends)
  - engine/core/conflict_detector.py
  - Gate 0 wiring in engine/engines/hk_engine.py
  - config/settings.py mode factory + clamp_orphan_age
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from botocore.stub import Stubber

import engine.core.conflict_detector as cd
import engine.engines.hk_engine as hk_engine_mod
import engine.utils.local_db as local_db
from config.settings import ORPHAN_MIN_AGE_HOURS_FLOOR, clamp_orphan_age, get_boto3_session, get_mode
from engine.core.execution_log_parquet import ParquetLogBuffer
from engine.core.lock_service import Lock, LockService
from engine.engines.hk_engine import HKEngine

TABLE_ROW = {
    "table_fqn":   "glue_catalog.finance_staging_db.gate0_test_tbl",
    "tier":        "standard",
    "domain":      "finance",
    "layer":       "staging",
    "environment": "prod",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def lock_db(tmp_path, monkeypatch):
    """Point the local SQLite backend at a throwaway DB with maintenance_locks."""
    import config.settings as settings

    db_path = tmp_path / "test_locks.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conn", None)

    conn = local_db.get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_locks (
            table_fqn    TEXT PRIMARY KEY,
            lock_owner   TEXT,
            operation    TEXT,
            acquired_at  TEXT,
            heartbeat_at TEXT,
            expires_at   INTEGER
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()
    monkeypatch.setattr(local_db, "_conn", None)


def _make_engine():
    eng = HKEngine(dry_run=True)
    eng._log_buffer    = ParquetLogBuffer(run_id=eng.run_id, engine="hk")
    eng._lock_service  = LockService(mode="local")
    return eng


# ══════════════════════════════════════════════════════════════════════════════
#  LockService — SQLite backend
# ══════════════════════════════════════════════════════════════════════════════

def test_acquire_contend_release_reacquire(lock_db):
    svc = LockService(mode="local")
    fqn = "glue_catalog.db.lock_t1"

    lock1 = svc.acquire(fqn, "hk_run")
    assert lock1 is not None

    lock2 = svc.acquire(fqn, "hk_run")
    assert lock2 is None  # contended -- still held by lock1

    svc.release(lock1)

    lock3 = svc.acquire(fqn, "hk_run")
    assert lock3 is not None


def test_expired_lock_is_stolen(lock_db):
    svc = LockService(mode="local")
    fqn = "glue_catalog.db.lock_t2"

    lock1 = svc.acquire(fqn, "hk_run", ttl_min=-1)  # already expired on arrival
    assert lock1 is not None

    lock2 = svc.acquire(fqn, "hk_run")
    assert lock2 is not None
    assert lock2.lock_owner != lock1.lock_owner


def test_heartbeat_extends_expiry(lock_db):
    svc = LockService(mode="local")
    lock = svc.acquire("glue_catalog.db.lock_t3", "hk_run")
    old_expiry = lock.expires_at

    ok = svc.heartbeat(lock, ttl_min=999)

    assert ok is True
    assert lock.expires_at > old_expiry


def test_wrong_owner_heartbeat_and_release_rejected(lock_db):
    svc = LockService(mode="local")
    fqn  = "glue_catalog.db.lock_t4"
    lock = svc.acquire(fqn, "hk_run")

    impostor = Lock(
        table_fqn=lock.table_fqn, lock_owner="someone-else", operation="hk_run",
        acquired_at=lock.acquired_at, heartbeat_at=lock.heartbeat_at,
        expires_at=lock.expires_at,
    )

    assert svc.heartbeat(impostor) is False
    svc.release(impostor)  # rejected -- must not raise, must not release

    # Real lock is still held -- a new acquire from another owner still contends
    assert svc.acquire(fqn, "hk_run") is None

    svc.release(lock)  # the actual owner can release
    assert svc.acquire(fqn, "hk_run") is not None


# ══════════════════════════════════════════════════════════════════════════════
#  LockService — DynamoDB backend (botocore Stubber)
# ══════════════════════════════════════════════════════════════════════════════

def test_ddb_acquire_success():
    svc    = LockService(mode="aws_ec2")
    client = svc._client()
    stubber = Stubber(client)
    stubber.add_response("put_item", {})
    stubber.activate()
    try:
        lock = svc.acquire("glue_catalog.db.ddb_t1", "hk_run")
        assert lock is not None
    finally:
        stubber.deactivate()


def test_ddb_acquire_conflict():
    svc    = LockService(mode="aws_ec2")
    client = svc._client()
    stubber = Stubber(client)
    stubber.add_client_error("put_item", service_error_code="ConditionalCheckFailedException")
    stubber.activate()
    try:
        lock = svc.acquire("glue_catalog.db.ddb_t2", "hk_run")
        assert lock is None
    finally:
        stubber.deactivate()


def test_ddb_heartbeat_and_release():
    svc    = LockService(mode="aws_ec2")
    client = svc._client()
    stubber = Stubber(client)
    stubber.add_response("update_item", {})
    stubber.add_response("delete_item", {})
    stubber.activate()
    try:
        now  = datetime.now(UTC)
        lock = Lock(
            table_fqn="glue_catalog.db.ddb_t3", lock_owner="me", operation="hk_run",
            acquired_at=now, heartbeat_at=now, expires_at=now + timedelta(minutes=1),
        )
        assert svc.heartbeat(lock) is True
        svc.release(lock)  # must not raise
    finally:
        stubber.deactivate()


def test_ddb_heartbeat_rejected_on_conflict():
    svc    = LockService(mode="aws_ec2")
    client = svc._client()
    stubber = Stubber(client)
    stubber.add_client_error("update_item", service_error_code="ConditionalCheckFailedException")
    stubber.activate()
    try:
        now  = datetime.now(UTC)
        lock = Lock(
            table_fqn="glue_catalog.db.ddb_t4", lock_owner="me", operation="hk_run",
            acquired_at=now, heartbeat_at=now, expires_at=now + timedelta(minutes=1),
        )
        assert svc.heartbeat(lock) is False
    finally:
        stubber.deactivate()


# ══════════════════════════════════════════════════════════════════════════════
#  Conflict Detector
# ══════════════════════════════════════════════════════════════════════════════

def test_check_with_cache_fresh_skips_live_call(monkeypatch):
    fresh_ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    df = pd.DataFrame([{
        "aws_opt_compaction": False, "aws_opt_retention": False,
        "aws_opt_orphan": False, "aws_opt_checked_at": fresh_ts,
    }])
    monkeypatch.setattr(cd, "read_sql", lambda *a, **k: df)

    def _must_not_be_called(*a, **k):
        raise AssertionError("check_table() must not run a live check when cache is fresh")
    monkeypatch.setattr(cd, "check_table", _must_not_be_called)

    result = cd.check_with_cache("glue_catalog.db.conflict_t1")
    assert result["source"] == "cache"
    assert result["conflict"] is False


def test_check_with_cache_stale_runs_live_and_writes_back(monkeypatch):
    stale_ts = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    df = pd.DataFrame([{
        "aws_opt_compaction": False, "aws_opt_retention": False,
        "aws_opt_orphan": False, "aws_opt_checked_at": stale_ts,
    }])
    monkeypatch.setattr(cd, "read_sql", lambda *a, **k: df)
    monkeypatch.setattr(
        cd, "check_table",
        lambda fqn: {"aws_opt_compaction": True, "aws_opt_retention": False, "aws_opt_orphan": False},
    )
    write_backs = []
    monkeypatch.setattr(cd, "run_query", lambda sql, **k: write_backs.append(sql))

    result = cd.check_with_cache("glue_catalog.db.conflict_t2")

    assert result["source"] == "live"
    assert result["conflict"] is True
    assert len(write_backs) == 1


def test_check_with_cache_missing_stamps_and_writes_back(monkeypatch):
    """No cache row at all -> treated as a miss, live check runs and writes back."""
    monkeypatch.setattr(cd, "read_sql", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        cd, "check_table",
        lambda fqn: {"aws_opt_compaction": False, "aws_opt_retention": False, "aws_opt_orphan": False},
    )
    write_backs = []
    monkeypatch.setattr(cd, "run_query", lambda sql, **k: write_backs.append(sql))

    result = cd.check_with_cache("glue_catalog.db.conflict_t3")

    assert result["source"] == "live"
    assert result["conflict"] is False
    assert len(write_backs) == 1


def test_any_optimizer_type_enabled_is_conflict(monkeypatch):
    monkeypatch.setattr(cd, "read_sql", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        cd, "check_table",
        lambda fqn: {"aws_opt_compaction": False, "aws_opt_retention": True, "aws_opt_orphan": False},
    )
    monkeypatch.setattr(cd, "run_query", lambda sql, **k: None)

    result = cd.check_with_cache("glue_catalog.db.conflict_t4")
    assert result["conflict"] is True


# ══════════════════════════════════════════════════════════════════════════════
#  Gate 0 — engine/engines/hk_engine.py
# ══════════════════════════════════════════════════════════════════════════════

def test_gate0_optimizer_conflict_skips(monkeypatch, lock_db):
    monkeypatch.setattr(hk_engine_mod, "get_hk_config", lambda fqn: {"policy_template": "TEST"})
    monkeypatch.setattr(hk_engine_mod, "check_with_cache", lambda fqn: {"conflict": True})

    eng = _make_engine()
    outcome = eng._process_table(dict(TABLE_ROW))

    assert outcome == "skipped"
    assert eng._log_buffer.entries[-1].skip_reason.startswith("SKIP_AWS_OPTIMIZER_CONFLICT")


def test_gate0_override_active_logs_and_proceeds(monkeypatch, lock_db):
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(hk_engine_mod, "get_hk_config", lambda fqn: {
        "gate0_override_until":  future,
        "gate0_override_reason": "incident-1234",
        "gate0_override_by":     "sujith",
    })
    monkeypatch.setattr(hk_engine_mod.execution_log, "get_running", lambda fqn: None)
    monkeypatch.setattr(
        HKEngine, "_run_gates_and_operations",
        lambda self, table_row, hk_config, table_dry_run: "succeeded",
    )

    eng = _make_engine()
    outcome = eng._process_table(dict(TABLE_ROW))

    assert outcome == "succeeded"
    gate0_entries = [e for e in eng._log_buffer.entries if e.operation == "gate0"]
    assert len(gate0_entries) == 1
    assert "GATE0_OVERRIDDEN" in gate0_entries[0].skip_reason
    assert "incident-1234" in gate0_entries[0].skip_reason


def test_gate0_already_running_skips(monkeypatch, lock_db):
    monkeypatch.setattr(hk_engine_mod, "get_hk_config", lambda fqn: {"policy_template": "TEST"})
    monkeypatch.setattr(hk_engine_mod, "check_with_cache", lambda fqn: {"conflict": False})
    monkeypatch.setattr(hk_engine_mod.execution_log, "get_running", lambda fqn: {"status": "RUNNING"})

    eng = _make_engine()
    outcome = eng._process_table(dict(TABLE_ROW))

    assert outcome == "skipped"
    assert eng._log_buffer.entries[-1].skip_reason == "SKIP_ALREADY_RUNNING"


def test_gate0_lock_held_skips(monkeypatch, lock_db):
    monkeypatch.setattr(hk_engine_mod, "get_hk_config", lambda fqn: {"policy_template": "TEST"})
    monkeypatch.setattr(hk_engine_mod, "check_with_cache", lambda fqn: {"conflict": False})
    monkeypatch.setattr(hk_engine_mod.execution_log, "get_running", lambda fqn: None)

    other = LockService(mode="local")
    held  = other.acquire(TABLE_ROW["table_fqn"], "hk_run")
    assert held is not None

    eng = _make_engine()
    outcome = eng._process_table(dict(TABLE_ROW))

    assert outcome == "skipped"
    assert eng._log_buffer.entries[-1].skip_reason == "SKIP_LOCK_HELD"


def test_gate0_clean_acquires_and_releases_lock(monkeypatch, lock_db):
    monkeypatch.setattr(hk_engine_mod, "get_hk_config", lambda fqn: {"policy_template": "TEST"})
    monkeypatch.setattr(hk_engine_mod, "check_with_cache", lambda fqn: {"conflict": False})
    monkeypatch.setattr(hk_engine_mod.execution_log, "get_running", lambda fqn: None)
    monkeypatch.setattr(
        HKEngine, "_run_gates_and_operations",
        lambda self, table_row, hk_config, table_dry_run: "succeeded",
    )

    eng = _make_engine()
    outcome = eng._process_table(dict(TABLE_ROW))
    assert outcome == "succeeded"

    # Lock must have been released in the finally block -- a fresh acquire succeeds.
    other = LockService(mode="local")
    assert other.acquire(TABLE_ROW["table_fqn"], "hk_run") is not None


# ══════════════════════════════════════════════════════════════════════════════
#  Settings — mode factory + orphan age clamp
# ══════════════════════════════════════════════════════════════════════════════

def test_get_mode_defaults_to_aws_ec2(monkeypatch):
    monkeypatch.delenv("ZAMBONI_MODE", raising=False)
    monkeypatch.delenv("ZAMBONI_LOCAL_MODE", raising=False)
    assert get_mode() == "aws_ec2"


def test_get_mode_legacy_local_flag(monkeypatch):
    monkeypatch.delenv("ZAMBONI_MODE", raising=False)
    monkeypatch.setenv("ZAMBONI_LOCAL_MODE", "true")
    assert get_mode() == "local"


def test_get_mode_explicit_override(monkeypatch):
    monkeypatch.setenv("ZAMBONI_MODE", "aws_local")
    assert get_mode() == "aws_local"


def test_get_boto3_session_returns_session(monkeypatch):
    monkeypatch.delenv("ZAMBONI_MODE", raising=False)
    monkeypatch.delenv("ZAMBONI_LOCAL_MODE", raising=False)
    import boto3
    assert isinstance(get_boto3_session(), boto3.Session)


def test_clamp_orphan_age_below_floor():
    assert clamp_orphan_age(24) == ORPHAN_MIN_AGE_HOURS_FLOOR


def test_clamp_orphan_age_above_floor():
    assert clamp_orphan_age(200) == 200


def test_clamp_orphan_age_at_floor():
    assert clamp_orphan_age(ORPHAN_MIN_AGE_HOURS_FLOOR) == ORPHAN_MIN_AGE_HOURS_FLOOR
