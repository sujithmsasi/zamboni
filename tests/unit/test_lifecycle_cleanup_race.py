"""
Regression test for engine/engines/lifecycle_engine.py::run_cleanup()'s
pre-delete exemption/claim re-check.

Real gap found in a 2026-07-09 audit: run_cleanup() fetches its PENDING_DROP
table list once at the start of the run, then loops through it calling
cleanup_table() (a real Glue DROP + S3 sweep) for each row using that
stale snapshot. If an owner exempts or claims a table (both flip
lifecycle_state to ACTIVE immediately -- see api/services/lifecycle_svc.py)
after the snapshot was taken but before that row's turn in the loop, the
table would be hard-deleted anyway on stale state. Fixed with a fresh
re-read of lifecycle_state immediately before the delete call.
"""
from __future__ import annotations

import pandas as pd
import pytest

import engine.engines.lifecycle_engine as lifecycle_engine_mod
import engine.utils.local_db as local_db
from engine.core.lock_service import LockService
from engine.engines.lifecycle_engine import LifecycleEngine


@pytest.fixture
def lock_db(tmp_path, monkeypatch):
    """Real SQLite-backed maintenance_locks table + LifecycleEngine wired to
    a local-mode LockService, so run_cleanup()'s lock acquisition
    (2026-07-09 audit fix) doesn't try to hit real AWS credentials."""
    import config.settings as settings

    db_path = tmp_path / "test_locks.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    conn = local_db.get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_locks (
            table_fqn    TEXT PRIMARY KEY,
            lock_owner   TEXT,
            operation    TEXT,
            acquired_at  TEXT,
            heartbeat_at TEXT,
            expires_at   TEXT
        )
        """
    )
    conn.commit()
    monkeypatch.setattr(lifecycle_engine_mod, "LockService", lambda: LockService(mode="local"))
    yield conn
    monkeypatch.setattr(local_db, "_conns", {})


def _pending_drop_row(fqn: str) -> dict:
    return {
        "table_fqn": fqn,
        "domain": "finance",
        "environment": "preprod",
        "lifecycle_state": "PENDING_DROP",
        "owner_exempted": 0,
        "pending_drop_expires_at": "2020-01-01 00:00:00",  # already expired
        "owner_email": "owner@company.com",
    }


def test_cleanup_skips_table_exempted_after_scan_but_before_delete(monkeypatch, lock_db):
    fqn = "glue_catalog.finance_preprod_db.fin_reconcile_test_copy"
    engine = LifecycleEngine(dry_run=False)

    # _get_pending_drop_tables()'s snapshot still shows PENDING_DROP...
    monkeypatch.setattr(
        lifecycle_engine_mod, "read_sql",
        lambda *a, **k: pd.DataFrame([{
            "lifecycle_state": "ACTIVE",  # ...but the fresh re-check sees the exemption
            "owner_exempted": 1,
            "pending_drop_expires_at": "2020-01-01 00:00:00",
        }]),
    )
    monkeypatch.setattr(engine, "_get_pending_drop_tables", lambda env: [_pending_drop_row(fqn)])

    cleanup_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod, "cleanup_table",
        lambda fqn, dry_run: cleanup_calls.append(fqn) or {"catalog_dropped": True, "s3_cleaned": True},
    )

    result = engine.run_cleanup(environment="preprod")

    assert cleanup_calls == [], "a table exempted after the scan must NOT be hard-deleted"
    assert result["succeeded"] == 0
    assert result["skipped"] == 1


def test_cleanup_proceeds_when_state_is_still_pending_drop(monkeypatch, lock_db):
    fqn = "glue_catalog.finance_preprod_db.fin_stale_copy"
    engine = LifecycleEngine(dry_run=False)

    monkeypatch.setattr(
        lifecycle_engine_mod, "read_sql",
        lambda *a, **k: pd.DataFrame([{
            "lifecycle_state": "PENDING_DROP",
            "owner_exempted": 0,
            "pending_drop_expires_at": "2020-01-01 00:00:00",
        }]),
    )
    monkeypatch.setattr(engine, "_get_pending_drop_tables", lambda env: [_pending_drop_row(fqn)])

    cleanup_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod, "cleanup_table",
        lambda fqn, dry_run: cleanup_calls.append(fqn) or {"catalog_dropped": True, "s3_cleaned": True, "bytes_reclaimed": 0},
    )
    monkeypatch.setattr(engine, "_mark_dropped", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_write_log", lambda *a, **k: None)

    result = engine.run_cleanup(environment="preprod")

    assert cleanup_calls == [fqn], "a table still genuinely PENDING_DROP must still be cleaned up"
    assert result["succeeded"] == 1


def test_cleanup_skips_table_whose_lock_is_held_by_another_engine(monkeypatch, lock_db):
    """A concurrent HK orchestrated run (or another cleanup run) holding
    the table's lock must block the hard delete, not race it."""
    fqn = "glue_catalog.finance_preprod_db.fin_locked_copy"
    engine = LifecycleEngine(dry_run=False)

    # Someone else already holds this table's lock.
    other_holder = LockService(mode="local").acquire(fqn, "orchestrated_maintenance")
    assert other_holder is not None

    monkeypatch.setattr(engine, "_get_pending_drop_tables", lambda env: [_pending_drop_row(fqn)])
    monkeypatch.setattr(engine, "_get_current_state", lambda fqn: {"lifecycle_state": "PENDING_DROP"})

    cleanup_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod, "cleanup_table",
        lambda fqn, dry_run: cleanup_calls.append(fqn) or {"catalog_dropped": True, "s3_cleaned": True},
    )

    result = engine.run_cleanup(environment="preprod")

    assert cleanup_calls == [], "must not delete a table whose lock is held elsewhere"
    assert result["skipped"] == 1
