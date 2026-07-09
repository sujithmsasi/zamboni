"""
Unit tests for scripts/control_plane_sync.py -- the one-way SQLite ->
Athena push for the 5 control-plane tables. No outbox/merge logic here
(unlike the retired athena_cache.py design) -- these tests exist mainly to
guard against accidentally reintroducing upsert/merge semantics that would
leak deleted rows, since a full-table overwrite is the whole point.
"""
from __future__ import annotations

import pytest

import config.settings as settings
import engine.core.control_plane as control_plane
import engine.utils.local_db as local_db
import scripts.control_plane_sync as sync_mod


@pytest.fixture
def cp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_sync_source.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(settings, "ZAMBONI_CONTROL_PLANE_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    conn = local_db.get_connection(str(db_path))
    conn.execute("CREATE TABLE domain_registry (domain_name TEXT PRIMARY KEY, is_active INTEGER)")
    conn.executemany(
        "INSERT INTO domain_registry (domain_name, is_active) VALUES (?, ?)",
        [("finance", 1), ("ers", 1)],
    )
    conn.commit()
    yield conn
    conn.close()
    monkeypatch.setattr(local_db, "_conns", {})


@pytest.fixture
def fake_to_iceberg(monkeypatch):
    """Capture every call instead of hitting real Athena."""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("awswrangler.athena.to_iceberg", _fake)
    monkeypatch.setattr(settings, "get_boto3_session", lambda: None)
    return calls


def test_sync_table_pushes_full_dataframe_with_overwrite_mode(cp_db, fake_to_iceberg):
    sync_mod.sync_table("domain_registry", "domain_registry")

    assert len(fake_to_iceberg) == 1
    call = fake_to_iceberg[0]
    assert call["mode"] == "overwrite"
    assert call["table"] == "domain_registry"
    assert len(call["df"]) == 2
    assert set(call["df"]["domain_name"]) == {"finance", "ers"}


def test_sync_table_reflects_a_deleted_row(cp_db, fake_to_iceberg):
    cp_db.execute("DELETE FROM domain_registry WHERE domain_name = 'ers'")
    cp_db.commit()

    sync_mod.sync_table("domain_registry", "domain_registry")

    call = fake_to_iceberg[0]
    assert len(call["df"]) == 1
    assert list(call["df"]["domain_name"]) == ["finance"]


def test_sync_table_pushes_zero_rows_when_table_emptied(cp_db, fake_to_iceberg):
    cp_db.execute("DELETE FROM domain_registry")
    cp_db.commit()

    sync_mod.sync_table("domain_registry", "domain_registry")

    call = fake_to_iceberg[0]
    assert len(call["df"]) == 0


def test_run_once_continues_past_a_failing_table(monkeypatch):
    calls = []

    def _fake_sync(bare_name, athena_table):
        calls.append(bare_name)
        if bare_name == "hk_config":
            raise RuntimeError("boom")

    monkeypatch.setattr(sync_mod, "sync_table", _fake_sync)
    sync_mod._run_once()

    assert calls == list(sync_mod._SYNCED_TABLES.keys())


def test_run_forever_returns_immediately_in_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", True)
    # If this doesn't return, the test hangs -- that's the assertion.
    sync_mod._run_forever()
