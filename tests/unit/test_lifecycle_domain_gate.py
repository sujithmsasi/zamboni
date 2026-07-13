"""
Regression tests for the 2026-07-09 domain-gating control on the
Lifecycle Engine: only a registered, ACTIVE domain_registry row should
have its tables scanned/candidate-marked, and a domain disabled at any
point -- even after a table already reached PENDING_DROP -- must block
the actual drop. This is stricter than registry.domain_active_filter_sql()
(a blocklist HK/Archival keep, see its docstring): an unregistered
domain -- one with no domain_registry row at all, not just an explicitly
deactivated one -- must also be excluded here, since the consequence of a
false positive is a hard Glue DROP + S3 sweep, not a skipped compaction.

Three independent gates are exercised, matching the three call sites in
engine/engines/lifecycle_engine.py:
  1. run_scan()                      -- discovery/marking
  2. _get_active_registry_tables() /
     _get_pending_drop_tables()      -- state-machine evaluation + the
                                         cleanup candidate fetch
  3. _get_current_state()'s pre-delete re-check inside run_cleanup()
                                      -- the one that matters most, since
                                         it's the last check before an
                                         irreversible delete.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

import config.settings as settings
import engine.engines.lifecycle_engine as lifecycle_engine_mod
import engine.utils.local_db as local_db
from config.control_plane_schema import CONTROL_PLANE_TABLES
from engine.core import registry
from engine.core.lock_service import LockService
from engine.engines.lifecycle_engine import LifecycleEngine
from engine.monitoring.activity_scanner import ActivitySignals

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def control_plane_db(tmp_path, monkeypatch):
    """Real domain_registry + nonprod_registry tables, real control-plane
    schema, production (non-local-mode) code path -- same convention as
    test_lifecycle_registry_upsert.py."""
    db_path = tmp_path / "test_control_plane.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(settings, "ZAMBONI_CONTROL_PLANE_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    local_db.create_tables(
        {
            "domain_registry": CONTROL_PLANE_TABLES["domain_registry"],
            "nonprod_registry": CONTROL_PLANE_TABLES["nonprod_registry"],
        },
        db_path=str(db_path),
    )
    yield db_path
    monkeypatch.setattr(local_db, "_conns", {})


@pytest.fixture
def cleanup_env(control_plane_db, monkeypatch, tmp_path):
    """Extends control_plane_db with a real maintenance_locks table (a
    physically separate SQLite file, matching production where locks and
    the config control plane are different stores) and wires
    LifecycleEngine to a local-mode LockService -- run_cleanup() needs
    both."""
    lock_path = tmp_path / "test_locks.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_DB", str(lock_path))
    conn = local_db.get_connection(str(lock_path))
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
    yield control_plane_db


def _insert_domain(db_path, domain_name: str, is_active: bool) -> None:
    conn = local_db.get_connection(str(db_path))
    conn.execute(
        "INSERT INTO domain_registry (domain_name, is_active) VALUES (?, ?)",
        (domain_name, 1 if is_active else 0),
    )
    conn.commit()


def _insert_nonprod_row(db_path, fqn: str, domain: str, lifecycle_state: str, **extra) -> None:
    conn = local_db.get_connection(str(db_path))
    row = {
        "table_fqn": fqn, "domain": domain, "environment": "preprod",
        "lifecycle_state": lifecycle_state, **extra,
    }
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO nonprod_registry ({cols}) VALUES ({placeholders})", tuple(row.values()))
    conn.commit()


def _candidate_row(fqn: str, domain: str) -> dict:
    """A cleanup candidate row shaped like _get_pending_drop_tables()'s
    real output -- used to bypass gate 2 directly so gate 3 (the
    pre-delete re-check) is the only thing under test."""
    return {
        "table_fqn": fqn, "domain": domain, "environment": "preprod",
        "pending_drop_expires_at": "2020-01-01 00:00:00",
    }


# ── registry.domain_registered_active_filter_sql() ──────────────────────────

def test_domain_registered_active_filter_sql_shape():
    clause = registry.domain_registered_active_filter_sql()
    assert "domain_registry" in clause
    assert "is_active = true" in clause
    assert "domain IN (SELECT domain_name" in clause


def test_domain_registered_active_filter_sql_custom_column():
    clause = registry.domain_registered_active_filter_sql("r.domain")
    assert clause.startswith("r.domain IN (SELECT domain_name")


# ── Gate 1: run_scan() ───────────────────────────────────────────────────────

def test_scan_skips_database_whose_domain_was_never_registered(monkeypatch, control_plane_db):
    _insert_domain(control_plane_db, "finance", is_active=True)
    # "membership" has no domain_registry row at all.

    get_tables_calls = []
    monkeypatch.setattr(lifecycle_engine_mod, "get_databases",
                         lambda force_refresh=False: ["finance_preprod_db", "membership_preprod_db"])
    monkeypatch.setattr(lifecycle_engine_mod, "get_tables",
                         lambda db, force_refresh=False: get_tables_calls.append(db) or [])

    engine = LifecycleEngine(dry_run=True)
    result = engine.run_scan(environment="preprod")

    assert get_tables_calls == ["finance_preprod_db"], \
        "an unregistered domain's database must never even be listed for tables"
    assert result["skipped"] == 1


def test_scan_skips_database_whose_domain_is_explicitly_inactive(monkeypatch, control_plane_db):
    _insert_domain(control_plane_db, "finance", is_active=True)
    _insert_domain(control_plane_db, "membership", is_active=False)

    get_tables_calls = []
    monkeypatch.setattr(lifecycle_engine_mod, "get_databases",
                         lambda force_refresh=False: ["finance_preprod_db", "membership_preprod_db"])
    monkeypatch.setattr(lifecycle_engine_mod, "get_tables",
                         lambda db, force_refresh=False: get_tables_calls.append(db) or [])

    engine = LifecycleEngine(dry_run=True)
    result = engine.run_scan(environment="preprod")

    assert get_tables_calls == ["finance_preprod_db"]
    assert result["skipped"] == 1


def test_scan_processes_database_whose_domain_is_active(monkeypatch, control_plane_db):
    _insert_domain(control_plane_db, "finance", is_active=True)

    monkeypatch.setattr(lifecycle_engine_mod, "get_databases", lambda force_refresh=False: ["finance_preprod_db"])
    monkeypatch.setattr(
        lifecycle_engine_mod, "get_tables",
        lambda db, force_refresh=False: [{"Name": "fin_reconcile", "CreateTime": datetime(2026, 1, 1, tzinfo=UTC)}],
    )
    monkeypatch.setattr(lifecycle_engine_mod, "is_iceberg_table", lambda t: True)
    monkeypatch.setattr(lifecycle_engine_mod, "is_backup_pattern", lambda n: (False, ""))
    monkeypatch.setattr(
        lifecycle_engine_mod, "get_activity_signals",
        lambda **k: ActivitySignals(table_fqn="x", days_since_activity=1),
    )

    engine = LifecycleEngine(dry_run=False)
    result = engine.run_scan(environment="preprod")

    assert result["skipped"] == 0
    assert result["discovered"] == 1


# ── Gate 2: _get_active_registry_tables() / _get_pending_drop_tables() ─────

def test_active_registry_tables_excludes_never_registered_domain(control_plane_db):
    _insert_domain(control_plane_db, "finance", is_active=True)
    _insert_nonprod_row(control_plane_db, "glue_catalog.finance_preprod_db.t1", "finance", "STALE_CANDIDATE")
    _insert_nonprod_row(control_plane_db, "glue_catalog.membership_preprod_db.t2", "membership", "STALE_CANDIDATE")

    engine = LifecycleEngine(dry_run=True)
    tables = engine._get_active_registry_tables(environment="preprod")

    fqns = {t["table_fqn"] for t in tables}
    assert fqns == {"glue_catalog.finance_preprod_db.t1"}, \
        "a table under a never-registered domain must not be evaluated toward a drop"


def test_pending_drop_tables_excludes_explicitly_disabled_domain(control_plane_db):
    _insert_domain(control_plane_db, "finance", is_active=False)
    _insert_nonprod_row(
        control_plane_db, "glue_catalog.finance_preprod_db.t1", "finance", "PENDING_DROP",
        pending_drop_expires_at="2020-01-01 00:00:00",
    )

    engine = LifecycleEngine(dry_run=True)
    tables = engine._get_pending_drop_tables(environment="preprod")

    assert tables == [], "a table whose domain is disabled must not be a cleanup candidate at all"


# ── Gate 3: run_cleanup()'s pre-delete re-check (the one that matters most) ─

def test_cleanup_refuses_to_drop_when_domain_becomes_inactive_after_the_scan(monkeypatch, cleanup_env):
    """The exact scenario reported: a table already legitimately reached
    PENDING_DROP while its domain was active (so it would have correctly
    passed gate 2's top-of-run fetch), but the domain was disabled before
    this table's turn in the cleanup loop. Gate 2 is bypassed here on
    purpose (already covered above) to isolate this specific TOCTOU
    window -- the real _get_current_state() re-check, against a real
    domain_registry table, must still refuse the drop."""
    db_path = cleanup_env
    _insert_domain(db_path, "finance", is_active=True)
    fqn = "glue_catalog.finance_preprod_db.fin_stale_copy"
    _insert_nonprod_row(db_path, fqn, "finance", "PENDING_DROP",
                        pending_drop_expires_at="2020-01-01 00:00:00")

    monkeypatch.setattr(
        LifecycleEngine, "_get_pending_drop_tables",
        lambda self, env: [_candidate_row(fqn, "finance")],
    )

    # Domain disabled "at this point" -- after the (simulated) scan above.
    conn = local_db.get_connection(str(db_path))
    conn.execute("UPDATE domain_registry SET is_active = 0 WHERE domain_name = 'finance'")
    conn.commit()

    cleanup_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod, "cleanup_table",
        lambda fqn, dry_run, cancel_check=None: cleanup_calls.append(fqn) or {"catalog_dropped": True, "s3_cleaned": True},
    )

    engine = LifecycleEngine(dry_run=False)
    result = engine.run_cleanup(environment="preprod")

    assert cleanup_calls == [], "a table whose domain became inactive after marking must NOT be dropped"
    assert result["skipped"] == 1


def test_cleanup_refuses_to_drop_when_domain_was_never_registered(monkeypatch, cleanup_env):
    db_path = cleanup_env
    # "finance" is never inserted into domain_registry at all.
    fqn = "glue_catalog.finance_preprod_db.fin_stale_copy2"
    _insert_nonprod_row(db_path, fqn, "finance", "PENDING_DROP",
                        pending_drop_expires_at="2020-01-01 00:00:00")

    monkeypatch.setattr(
        LifecycleEngine, "_get_pending_drop_tables",
        lambda self, env: [_candidate_row(fqn, "finance")],
    )

    cleanup_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod, "cleanup_table",
        lambda fqn, dry_run, cancel_check=None: cleanup_calls.append(fqn) or {"catalog_dropped": True, "s3_cleaned": True},
    )

    engine = LifecycleEngine(dry_run=False)
    engine.run_cleanup(environment="preprod")

    assert cleanup_calls == [], \
        "a table under a domain with no domain_registry row at all must not be dropped either"


def test_cleanup_still_drops_when_domain_remains_active(monkeypatch, cleanup_env):
    """Positive control: proves gate 3 isn't a tautology that skips
    everything regardless of domain state."""
    db_path = cleanup_env
    _insert_domain(db_path, "finance", is_active=True)
    fqn = "glue_catalog.finance_preprod_db.fin_stale_copy3"
    _insert_nonprod_row(db_path, fqn, "finance", "PENDING_DROP",
                        pending_drop_expires_at="2020-01-01 00:00:00")

    monkeypatch.setattr(
        LifecycleEngine, "_get_pending_drop_tables",
        lambda self, env: [_candidate_row(fqn, "finance")],
    )

    cleanup_calls = []
    monkeypatch.setattr(
        lifecycle_engine_mod, "cleanup_table",
        lambda fqn, dry_run, cancel_check=None: cleanup_calls.append(fqn) or {"catalog_dropped": True, "s3_cleaned": True, "bytes_reclaimed": 0},
    )
    monkeypatch.setattr(LifecycleEngine, "_mark_dropped", lambda self, *a, **k: None)
    monkeypatch.setattr(LifecycleEngine, "_write_log", lambda self, *a, **k: None)

    engine = LifecycleEngine(dry_run=False)
    result = engine.run_cleanup(environment="preprod")

    assert cleanup_calls == [fqn], "a table under a domain that's still active must still be cleaned up"
    assert result["succeeded"] == 1
