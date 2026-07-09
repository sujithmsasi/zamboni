"""
Unit tests for engine/core/control_plane.py -- the SQLite-primary
read/write module for stream_registry, hk_config, domain_registry,
nonprod_registry, and controlm_jobs. Replaces the retired
tests/unit/test_athena_cache.py (engine/core/athena_cache.py was built on
the opposite assumption -- Athena primary, SQLite a lagging mirror -- and
is gone, not adapted).
"""
from __future__ import annotations

import sqlite3

import pytest

import config.settings as settings
import engine.core.control_plane as control_plane
import engine.utils.local_db as local_db


@pytest.fixture
def cp_db(tmp_path, monkeypatch):
    """Point ZAMBONI_CONTROL_PLANE_DB at a throwaway file with a minimal
    stream_registry table, ZAMBONI_LOCAL_MODE=False (the "production"
    path, exercising the branch local-mode tests don't)."""
    db_path = tmp_path / "test_control_plane.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(settings, "ZAMBONI_CONTROL_PLANE_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    conn = local_db.get_connection(str(db_path))
    conn.execute(
        """
        CREATE TABLE stream_registry (
            table_fqn  TEXT PRIMARY KEY,
            domain     TEXT,
            hk_enabled INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO stream_registry (table_fqn, domain, hk_enabled) VALUES (?, ?, ?)",
        ("glue_catalog.db.t1", "finance", 0),
    )
    conn.commit()
    yield conn
    conn.close()
    monkeypatch.setattr(local_db, "_conns", {})


@pytest.fixture
def local_mode_db(tmp_path, monkeypatch):
    """ZAMBONI_LOCAL_MODE=True -- control_plane._db_path() must resolve to
    ZAMBONI_LOCAL_DB, not ZAMBONI_CONTROL_PLANE_DB, so local/demo behavior
    is byte-for-byte unchanged by this module's existence."""
    db_path = tmp_path / "test_local.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", True)
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_DB", str(db_path))
    # A distinct, deliberately-wrong control-plane path -- if _db_path()
    # picked this by mistake, the table below wouldn't exist there and
    # every read/write would fail loudly instead of silently passing.
    monkeypatch.setattr(settings, "ZAMBONI_CONTROL_PLANE_DB", str(tmp_path / "wrong.db"))
    monkeypatch.setattr(local_db, "_conns", {})

    conn = local_db.get_connection(str(db_path))
    conn.execute("CREATE TABLE stream_registry (table_fqn TEXT PRIMARY KEY, domain TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO stream_registry (table_fqn, domain) VALUES ('t1', 'finance')")
    conn.commit()
    yield conn
    conn.close()
    monkeypatch.setattr(local_db, "_conns", {})


# ── _db_path() routing ───────────────────────────────────────────────────────

def test_db_path_resolves_to_control_plane_db_outside_local_mode(cp_db):
    assert control_plane._db_path() == str(settings.ZAMBONI_CONTROL_PLANE_DB)


def test_db_path_resolves_to_local_db_in_local_mode(local_mode_db):
    assert control_plane._db_path() == str(settings.ZAMBONI_LOCAL_DB)


# ── read_sql / run_query ─────────────────────────────────────────────────────

def test_read_sql_returns_rows(cp_db):
    df = control_plane.read_sql("SELECT * FROM stream_registry")
    assert len(df) == 1
    assert df.iloc[0]["table_fqn"] == "glue_catalog.db.t1"


def test_read_sql_routes_through_local_mode_db(local_mode_db):
    df = control_plane.read_sql("SELECT * FROM stream_registry")
    assert len(df) == 1
    assert df.iloc[0]["table_fqn"] == "t1"


def test_run_query_dry_run_is_noop(cp_db):
    result = control_plane.run_query(
        "UPDATE stream_registry SET hk_enabled = 1 WHERE table_fqn = 'glue_catalog.db.t1'",
        dry_run=True,
    )
    assert result is None
    row = cp_db.execute("SELECT hk_enabled FROM stream_registry WHERE table_fqn = 'glue_catalog.db.t1'").fetchone()
    assert row[0] == 0


def test_run_query_real_write_applies(cp_db):
    result = control_plane.run_query(
        "UPDATE stream_registry SET hk_enabled = 1 WHERE table_fqn = 'glue_catalog.db.t1'",
        dry_run=False,
    )
    assert result is not None
    row = cp_db.execute("SELECT hk_enabled FROM stream_registry WHERE table_fqn = 'glue_catalog.db.t1'").fetchone()
    assert row[0] == 1


# ── _sql_literal / _esc ──────────────────────────────────────────────────────

def test_sql_literal_bool():
    assert control_plane._sql_literal(True) == "1"
    assert control_plane._sql_literal(False) == "0"


def test_sql_literal_int():
    assert control_plane._sql_literal(42) == "42"


def test_sql_literal_none():
    assert control_plane._sql_literal(None) == "NULL"


def test_sql_literal_string_escapes_quotes():
    assert control_plane._sql_literal("O'Brien") == "'O''Brien'"


# ── update_row ───────────────────────────────────────────────────────────────

def test_update_row_empty_column_values_is_noop(cp_db):
    control_plane.update_row("stream_registry", "table_fqn", "glue_catalog.db.t1", {}, dry_run=False)
    row = cp_db.execute("SELECT domain FROM stream_registry WHERE table_fqn = 'glue_catalog.db.t1'").fetchone()
    assert row[0] == "finance"


def test_update_row_writes_columns_and_stamps_updated_at(cp_db):
    control_plane.update_row(
        "stream_registry", "table_fqn", "glue_catalog.db.t1",
        {"domain": "ers", "hk_enabled": True},
        dry_run=False,
    )
    row = cp_db.execute(
        "SELECT domain, hk_enabled, updated_at FROM stream_registry WHERE table_fqn = 'glue_catalog.db.t1'"
    ).fetchone()
    assert row[0] == "ers"
    assert row[1] == 1
    assert row[2] is not None


def test_update_row_touch_updated_at_false_skips_stamp(cp_db):
    control_plane.update_row(
        "stream_registry", "table_fqn", "glue_catalog.db.t1",
        {"domain": "ers"},
        dry_run=False, touch_updated_at=False,
    )
    row = cp_db.execute("SELECT updated_at FROM stream_registry WHERE table_fqn = 'glue_catalog.db.t1'").fetchone()
    assert row[0] is None


def test_update_row_dry_run_does_not_write(cp_db):
    control_plane.update_row(
        "stream_registry", "table_fqn", "glue_catalog.db.t1",
        {"domain": "ers"},
        dry_run=True,
    )
    row = cp_db.execute("SELECT domain FROM stream_registry WHERE table_fqn = 'glue_catalog.db.t1'").fetchone()
    assert row[0] == "finance"


def test_update_row_escapes_key_value(cp_db):
    cp_db.execute("INSERT INTO stream_registry (table_fqn, domain) VALUES (?, ?)", ("o'brien.t", "x"))
    cp_db.commit()
    control_plane.update_row("stream_registry", "table_fqn", "o'brien.t", {"domain": "y"}, dry_run=False)
    row = cp_db.execute("SELECT domain FROM stream_registry WHERE table_fqn = ?", ("o'brien.t",)).fetchone()
    assert row[0] == "y"
