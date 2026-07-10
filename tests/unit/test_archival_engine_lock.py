"""
Regression test for engine/engines/archival_engine.py's lock-service usage.

Real gap found in a 2026-07-09 audit: neither ArchivalEngine nor
LifecycleEngine acquired the maintenance lock HKEngine/orchestrator.py
already uses, so an archival run and a concurrent HK orchestrated run (or
two archival retries) could mutate the same table's Iceberg metadata at
the same time. The lock is keyed on table_fqn alone
(engine/core/lock_service.py::acquire), so a lock acquired here correctly
contends against a lock already held by orchestrator.py for the same table.
"""
from __future__ import annotations

import pytest

import engine.utils.local_db as local_db
from engine.core.lock_service import LockService
from engine.engines.archival_engine import ArchivalEngine


@pytest.fixture
def lock_db(tmp_path, monkeypatch):
    """Real SQLite-backed maintenance_locks table, same shape
    test_safety_core.py's fixture uses."""
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
    yield conn
    monkeypatch.setattr(local_db, "_conns", {})


def test_process_table_skips_when_lock_already_held(monkeypatch, lock_db):
    fqn = "glue_catalog.finance_db.finance_staging"
    monkeypatch.setattr(
        "engine.engines.archival_engine.LockService",
        lambda: LockService(mode="local"),
    )
    holder = LockService(mode="local").acquire(fqn, "orchestrated_maintenance")
    assert holder is not None  # sanity: the other "engine" really holds it

    discover_calls = []
    monkeypatch.setattr(
        "engine.engines.archival_engine.discover_cold_partitions",
        lambda **kw: discover_calls.append(kw) or [],
    )

    engine = ArchivalEngine(dry_run=True)
    result = engine._process_table({"table_fqn": fqn, "archive_retention_days": 30})

    assert result["skipped"] == 1
    assert result["succeeded"] == 0 and result["failed"] == 0
    assert discover_calls == [], "must not touch the table while another engine holds its lock"


def test_process_table_acquires_and_releases_lock_when_free(monkeypatch, lock_db):
    fqn = "glue_catalog.finance_db.finance_staging"
    monkeypatch.setattr(
        "engine.engines.archival_engine.LockService",
        lambda: LockService(mode="local"),
    )
    monkeypatch.setattr(
        "engine.engines.archival_engine._resolve_partition_column",
        lambda *a, **k: "partition_date",
    )
    monkeypatch.setattr(
        "engine.engines.archival_engine.discover_cold_partitions",
        lambda **kw: [],  # nothing to archive -- just proving the lock cycle
    )

    engine = ArchivalEngine(dry_run=True)
    result = engine._process_table({"table_fqn": fqn, "archive_retention_days": 30})

    assert result == {"partitions_found": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    # Lock must be released afterward -- a fresh acquire must succeed.
    reacquired = LockService(mode="local").acquire(fqn, "orchestrated_maintenance")
    assert reacquired is not None, "archival must release its lock when done, not hold it"
