"""
Regression test for engine/engines/lifecycle_engine.py::_upsert_nonprod_registry.

Real, reproducible bug found in a 2026-07-09 audit: this function's INSERT
was a positional VALUES tuple written against the legacy 29-column
sql/create_nonprod_registry.sql Athena DDL, but lifecycle_engine.py has
written nonprod_registry through engine.core.control_plane (SQLite-primary,
27-column config/control_plane_schema.py shape) since that same day's
control-plane migration -- a column-count mismatch that made every new
non-prod table discovery fail silently (caught by run_scan's per-table
try/except). The UPDATE branch separately referenced last_scanned_at, a
column that was never part of the new schema either, so refreshing an
already-registered table's activity signals failed too. Both are exercised
here against a real SQLite file with the real schema -- no mocks of the
write path itself.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

import config.settings as settings
import engine.utils.local_db as local_db
from config.control_plane_schema import CONTROL_PLANE_TABLES
from engine.engines.lifecycle_engine import LifecycleEngine
from engine.monitoring.activity_scanner import ActivitySignals


@pytest.fixture
def registry_db(tmp_path, monkeypatch):
    """Real nonprod_registry table, real control-plane schema, production
    (non-local-mode) code path."""
    db_path = tmp_path / "test_control_plane.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(settings, "ZAMBONI_CONTROL_PLANE_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    local_db.create_tables(
        {"nonprod_registry": CONTROL_PLANE_TABLES["nonprod_registry"]},
        db_path=str(db_path),
    )
    yield db_path
    monkeypatch.setattr(local_db, "_conns", {})


def _glue_table(name: str) -> dict:
    return {"Name": name, "CreateTime": datetime(2026, 1, 1, tzinfo=UTC)}


def test_upsert_inserts_new_table(registry_db):
    engine = LifecycleEngine(dry_run=False)
    signals = ActivitySignals(
        table_fqn="glue_catalog.finance_dev_db.fin_reconcile_dev",
        last_query_at=datetime(2026, 7, 1, tzinfo=UTC),
        last_write_at=datetime(2026, 7, 2, tzinfo=UTC),
        days_since_activity=8,
    )
    with (
        patch("engine.engines.lifecycle_engine.is_iceberg_table", return_value=True),
        patch("engine.engines.lifecycle_engine.is_backup_pattern", return_value=(False, "")),
        patch("engine.engines.lifecycle_engine.get_activity_signals", return_value=signals),
    ):
        engine._upsert_nonprod_registry(_glue_table("fin_reconcile_dev"), "finance_dev_db", "dev")

    conn = local_db.get_connection(str(registry_db))
    row = conn.execute(
        "SELECT domain, environment, lifecycle_state, days_since_activity, scan_count "
        "FROM nonprod_registry WHERE table_fqn = ?",
        ("glue_catalog.finance_dev_db.fin_reconcile_dev",),
    ).fetchone()

    assert row is not None, "new table should have been inserted, not silently dropped"
    assert row[0] == "finance"
    assert row[1] == "dev"
    assert row[2] == "ACTIVE"
    assert row[3] == 8
    assert row[4] == 1


def test_upsert_refreshes_existing_table_without_touching_lifecycle_state(registry_db):
    engine = LifecycleEngine(dry_run=False)
    fqn = "glue_catalog.membership_uat_db.mbr_activity_uat"
    first_signals = ActivitySignals(table_fqn=fqn, days_since_activity=20)
    with (
        patch("engine.engines.lifecycle_engine.is_iceberg_table", return_value=True),
        patch("engine.engines.lifecycle_engine.is_backup_pattern", return_value=(False, "")),
        patch("engine.engines.lifecycle_engine.get_activity_signals", return_value=first_signals),
    ):
        engine._upsert_nonprod_registry(_glue_table("mbr_activity_uat"), "membership_uat_db", "preprod")

    # Simulate the state machine having moved this table on since first scan.
    conn = local_db.get_connection(str(registry_db))
    conn.execute(
        "UPDATE nonprod_registry SET lifecycle_state = 'STALE_CANDIDATE' "
        "WHERE table_fqn = 'glue_catalog.membership_uat_db.mbr_activity_uat'"
    )
    conn.commit()

    second_signals = ActivitySignals(table_fqn=fqn, days_since_activity=21)
    with (
        patch("engine.engines.lifecycle_engine.is_iceberg_table", return_value=True),
        patch("engine.engines.lifecycle_engine.is_backup_pattern", return_value=(False, "")),
        patch("engine.engines.lifecycle_engine.get_activity_signals", return_value=second_signals),
    ):
        engine._upsert_nonprod_registry(_glue_table("mbr_activity_uat"), "membership_uat_db", "preprod")

    row = conn.execute(
        "SELECT lifecycle_state, days_since_activity, scan_count FROM nonprod_registry "
        "WHERE table_fqn = 'glue_catalog.membership_uat_db.mbr_activity_uat'"
    ).fetchone()

    assert row[0] == "STALE_CANDIDATE", "a re-scan must never reset the lifecycle state machine"
    assert row[1] == 21, "activity signal should refresh to the latest scan's value"
    assert row[2] == 2, "scan_count should increment, not reset"
