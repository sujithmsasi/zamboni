"""
Unit tests for scripts/control_plane_sync.py -- the one-way SQLite ->
Athena push for the 5 control-plane tables. No outbox/merge logic here
(unlike the retired athena_cache.py design) -- these tests exist mainly to
guard against accidentally reintroducing upsert/merge semantics that would
leak deleted rows, since a full-table overwrite is the whole point.
"""
from __future__ import annotations

import pandas as pd
import pytest

import config.settings as settings
import engine.core.control_plane as control_plane
import engine.utils.athena_client as athena_client
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


@pytest.fixture
def stream_registry_db(tmp_path, monkeypatch):
    """stream_registry's SQLite mirror deliberately has no aws_opt_*/
    last_execution_id/metadata_location/properties_synced columns -- this
    fixture matches that (see config/control_plane_schema.py)."""
    db_path = tmp_path / "test_sync_source_sr.db"
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(settings, "ZAMBONI_CONTROL_PLANE_DB", str(db_path))
    monkeypatch.setattr(local_db, "_conns", {})

    conn = local_db.get_connection(str(db_path))
    conn.execute(
        "CREATE TABLE stream_registry (table_fqn TEXT PRIMARY KEY, domain TEXT, hk_enabled INTEGER)"
    )
    conn.execute(
        "INSERT INTO stream_registry (table_fqn, domain, hk_enabled) VALUES (?, ?, ?)",
        ("glue_catalog.finance_db.fin_table", "finance", 1),
    )
    conn.commit()
    yield conn
    conn.close()
    monkeypatch.setattr(local_db, "_conns", {})


def test_sync_table_stream_registry_preserves_engine_owned_columns_from_athena(
    stream_registry_db, fake_to_iceberg, monkeypatch
):
    """
    Real bug fix (2026-07-10): sync_table()'s SELECT * against the SQLite
    mirror omits engine-owned columns the real Athena table still has --
    without this merge, the full-table overwrite would either fail on the
    schema mismatch or silently drop Gate 0's conflict cache / idempotency
    / recovery state from the real table. Asserting outside the
    monkeypatched fetch function (not inside it) so an assertion failure
    surfaces as a test failure instead of being swallowed by
    _fetch_engine_owned_columns' own except Exception.
    """
    captured_sql: list[str] = []

    def _fake_athena_read_sql(sql, workgroup="app", **kwargs):
        captured_sql.append(sql)
        return pd.DataFrame([{
            "table_fqn": "glue_catalog.finance_db.fin_table",
            "aws_opt_compaction": 1,
            "aws_opt_retention": 0,
            "aws_opt_orphan": 0,
            "aws_opt_checked_at": "2026-07-09 10:00:00",
            "last_execution_id": "abc123",
            "metadata_location": "s3://bucket/metadata/001.json",
            "properties_synced": 1,
        }])

    monkeypatch.setattr(athena_client, "read_sql", _fake_athena_read_sql)

    sync_mod.sync_table("stream_registry", "stream_registry")

    assert len(captured_sql) == 1
    assert "aws_opt_compaction" in captured_sql[0]

    df = fake_to_iceberg[0]["df"]
    assert len(df) == 1
    row = df.iloc[0]
    assert row["aws_opt_compaction"] == 1
    assert row["last_execution_id"] == "abc123"
    assert row["metadata_location"] == "s3://bucket/metadata/001.json"
    # SQLite-sourced columns must still be present, not replaced.
    assert row["domain"] == "finance"
    assert row["hk_enabled"] == 1


def test_sync_table_stream_registry_defaults_engine_owned_columns_to_null_when_table_not_found(
    stream_registry_db, fake_to_iceberg, monkeypatch
):
    """A fresh environment's very first sync -- the real Athena
    stream_registry table doesn't exist yet -- must not crash the sync
    cycle or silently omit the engine-owned columns -- they come through
    as null so the table still gets created/overwritten with the right
    column set. Only a table-not-found-shaped error takes this path (see
    the next test for any other kind of failure)."""
    def _raise(*_args, **_kwargs):
        raise RuntimeError("TABLE_NOT_FOUND: line 1:15: Table does not exist")

    monkeypatch.setattr(athena_client, "read_sql", _raise)

    sync_mod.sync_table("stream_registry", "stream_registry")

    df = fake_to_iceberg[0]["df"]
    assert len(df) == 1
    row = df.iloc[0]
    assert row["aws_opt_compaction"] is None
    assert row["last_execution_id"] is None
    assert row["metadata_location"] is None
    assert row["domain"] == "finance"


def test_sync_table_stream_registry_aborts_on_unexpected_fetch_error(
    stream_registry_db, fake_to_iceberg, monkeypatch
):
    """
    Real gap closed (2026-07-10): the first version of this fix treated
    ANY engine-owned-columns fetch failure the same as "table not found"
    and pushed the overwrite anyway with those columns nulled -- correct
    for a genuinely fresh table, but wrong for a transient failure (an
    IAM/workgroup quirk, throttling) where the real Athena data is fine
    and nulling it would be actively destructive. A non-table-not-found
    error must now abort the whole sync for this table -- no overwrite
    pushed at all -- rather than silently nulling real data.
    """
    def _raise(*_args, **_kwargs):
        raise RuntimeError("Access Denied: not authorized to perform athena:GetQueryResults")

    monkeypatch.setattr(athena_client, "read_sql", _raise)

    with pytest.raises(sync_mod.EngineOwnedColumnsUnavailable):
        sync_mod.sync_table("stream_registry", "stream_registry")

    # No overwrite must have been pushed -- aborting before to_iceberg is
    # the whole point, not nulling-then-pushing.
    assert fake_to_iceberg == []


def test_sync_table_non_stream_registry_table_never_calls_athena(cp_db, fake_to_iceberg, monkeypatch):
    """domain_registry has no engine-owned columns -- the merge path must
    be a complete no-op for it, including never calling out to Athena."""
    called = []

    def _fake_athena_read_sql(*_args, **_kwargs):
        called.append(1)
        return pd.DataFrame()

    monkeypatch.setattr(athena_client, "read_sql", _fake_athena_read_sql)

    sync_mod.sync_table("domain_registry", "domain_registry")

    assert called == []
    assert len(fake_to_iceberg[0]["df"]) == 2
