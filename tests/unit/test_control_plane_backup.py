"""
Unit tests for scripts/control_plane_backup.py -- VACUUM INTO -> S3
snapshots and the two-tier (hourly/daily) retention prune.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import config.settings as settings
import scripts.control_plane_backup as backup_mod


def _key(dt: datetime) -> str:
    return f"control-plane-backups/zamboni_control_{dt.strftime('%Y%m%dT%H%M%SZ')}.db"


# ── take_backup ──────────────────────────────────────────────────────────────

def test_take_backup_vacuums_a_real_sqlite_file_and_uploads(tmp_path, monkeypatch):
    src = tmp_path / "source.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (name) VALUES ('hello')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(backup_mod, "ZAMBONI_CONTROL_PLANE_DB", str(src))
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/meta/")

    uploads = []

    def _fake_upload(bucket, key, local_path):
        # Verify the temp file is a real, valid, non-empty SQLite backup
        # -- taken via VACUUM INTO, not a placeholder or empty file.
        c = sqlite3.connect(local_path)
        rows = c.execute("SELECT name FROM t").fetchall()
        c.close()
        uploads.append((bucket, key, rows))

    monkeypatch.setattr(backup_mod, "upload_file", _fake_upload)

    key = backup_mod.take_backup()

    assert len(uploads) == 1
    bucket, uploaded_key, rows = uploads[0]
    assert bucket == "test-bucket"
    assert uploaded_key == key
    assert key.startswith("meta/control-plane-backups/zamboni_control_")
    assert key.endswith(".db")
    assert rows == [("hello",)]


# ── prune_backups ────────────────────────────────────────────────────────────

def test_prune_backups_keeps_newest_per_hour_and_per_day(monkeypatch):
    now = datetime.now(UTC)

    # Anchored to a fixed minute/hour-of-day rather than raw offsets from
    # `now` -- two keys a few minutes/hours apart from `now` can straddle
    # an hour/day boundary depending on what minute/hour `now` happens to
    # be at test-run time (found as a real, reproducible flake while
    # verifying an unrelated fix -- e.g. hours=2,minutes=40 vs
    # hours=2,minutes=10 land in different hour buckets whenever `now`'s
    # own minute is in [10, 40)). Anchoring guarantees the same-hour/
    # same-day pairing by construction instead of by luck.

    # Within the 24h hourly window: two backups in the same hour -- only
    # the newest should survive.
    hour_anchor = (now - timedelta(hours=2)).replace(minute=30, second=0, microsecond=0)
    hour_old_key = _key(hour_anchor - timedelta(minutes=10))
    hour_new_key = _key(hour_anchor + timedelta(minutes=10))
    # Within the 30d daily window (past 24h): two backups on the same day
    # -- only the newest should survive.
    day_anchor = (now - timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
    day_old_key = _key(day_anchor - timedelta(hours=1))
    day_new_key = _key(day_anchor + timedelta(hours=1))
    # Older than the 30d window entirely -- always deleted.
    ancient_key = _key(now - timedelta(days=45))

    all_keys = [hour_old_key, hour_new_key, day_old_key, day_new_key, ancient_key]

    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: list(all_keys))

    deleted_calls = []
    monkeypatch.setattr(backup_mod, "delete_keys", lambda bucket, keys: deleted_calls.extend(keys) or len(keys))

    deleted_count = backup_mod.prune_backups(hourly_retention_hours=24, daily_retention_days=30)

    assert hour_old_key in deleted_calls
    assert hour_new_key not in deleted_calls
    assert day_old_key in deleted_calls
    assert day_new_key not in deleted_calls
    assert ancient_key in deleted_calls
    assert deleted_count == 3


def test_prune_backups_ignores_keys_not_matching_backup_pattern(monkeypatch):
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: ["control-plane-backups/README.txt"])

    deleted_calls = []
    monkeypatch.setattr(backup_mod, "delete_keys", lambda bucket, keys: deleted_calls.extend(keys) or len(keys))

    deleted_count = backup_mod.prune_backups()

    assert deleted_calls == []
    assert deleted_count == 0


def test_prune_backups_noop_when_nothing_to_prune(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: [_key(now)])

    called = []
    monkeypatch.setattr(backup_mod, "delete_keys", lambda bucket, keys: called.append(keys) or len(keys))

    deleted_count = backup_mod.prune_backups()

    assert deleted_count == 0
    # delete_keys is always called (its own empty-list early-return handles
    # the no-op case) but with nothing to delete -- a single fresh backup
    # is the newest (and only) entry in its hour bucket.
    assert called == [[]]


# ── _run_forever local-mode guard ────────────────────────────────────────────

def test_run_forever_returns_immediately_in_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "ZAMBONI_LOCAL_MODE", True)
    backup_mod._run_forever()


# ── list_backups / restore_latest (2026-07-10 audit fix) ─────────────────────
#
# Real gap closed: take_backup()/prune_backups() ran on schedule, but
# nothing in this codebase ever read a backup back. Combined with
# deploy/zamboni-cfn.yaml's DeleteOnTermination=true root volume, a real
# EC2 instance replacement would silently start the control-plane DB
# empty with no way back -- these two functions are the missing restore
# half of the backup story.

def test_list_backups_sorted_newest_first(monkeypatch):
    now = datetime.now(UTC)
    older = _key(now - timedelta(hours=5))
    newer = _key(now - timedelta(hours=1))
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: [older, newer])

    backups = backup_mod.list_backups()

    assert [key for _, key in backups] == [newer, older]


def test_list_backups_empty_when_none_exist(monkeypatch):
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: [])

    assert backup_mod.list_backups() == []


def _write_valid_control_plane_db(path: str) -> None:
    """A minimal (empty-rows) but schema-valid control-plane SQLite file
    -- passes both PRAGMA integrity_check and the expected-table check."""
    from config.control_plane_schema import CONTROL_PLANE_TABLES
    conn = sqlite3.connect(path)
    for ddl in CONTROL_PLANE_TABLES.values():
        conn.execute(ddl)
    conn.commit()
    conn.close()


def test_restore_latest_downloads_newest_backup(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    older = _key(now - timedelta(hours=5))
    newer = _key(now - timedelta(hours=1))
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/meta/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: [older, newer])

    downloads = []

    def _fake_download(bucket, key, path):
        downloads.append((bucket, key, path))
        _write_valid_control_plane_db(path)

    monkeypatch.setattr(backup_mod, "download_file", _fake_download)

    dest = str(tmp_path / "restored.db")
    restored_key = backup_mod.restore_latest(dest)

    assert restored_key == newer
    assert len(downloads) == 1
    bucket, key, path = downloads[0]
    assert bucket == "test-bucket"
    assert key == newer
    assert path != dest, "must download to a temp candidate first, never straight onto dest"
    assert Path(dest).exists()


# ── restore validation: PRAGMA integrity_check + schema + atomic replace ─────
# (2026-07-11 audit fix)

def test_restore_key_rejects_corrupted_backup_and_leaves_dest_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")

    def _fake_download(bucket, key, path):
        Path(path).write_bytes(b"this is not a sqlite file at all")

    monkeypatch.setattr(backup_mod, "download_file", _fake_download)

    dest = tmp_path / "control.db"
    dest.write_text("original content")

    with pytest.raises(backup_mod.BackupValidationError):
        backup_mod._restore_key("some-key.db", str(dest))

    assert dest.read_text() == "original content", "dest must be untouched when the backup is corrupted"
    # No stray temp candidate left behind either.
    leftovers = list(tmp_path.glob(".restore_candidate_*"))
    assert leftovers == []


def test_restore_key_rejects_schema_incompatible_backup(tmp_path, monkeypatch):
    """A backup missing one of the 5 expected control-plane tables (taken
    by an older/incompatible schema version) must be rejected, not
    silently accepted just because SQLite itself can open it."""
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")

    def _fake_download(bucket, key, path):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE domain_registry (domain_name TEXT PRIMARY KEY)")
        # Missing stream_registry/hk_config/nonprod_registry/controlm_jobs.
        conn.commit()
        conn.close()

    monkeypatch.setattr(backup_mod, "download_file", _fake_download)

    dest = tmp_path / "control.db"

    with pytest.raises(backup_mod.BackupValidationError, match="missing expected table"):
        backup_mod._restore_key("some-key.db", str(dest))

    assert not dest.exists()


def test_restore_key_atomically_replaces_dest_on_valid_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "download_file", lambda bucket, key, path: _write_valid_control_plane_db(path))

    dest = tmp_path / "control.db"
    dest.write_text("stale placeholder")

    backup_mod._restore_key("some-key.db", str(dest))

    # dest is now a real, valid control-plane SQLite file, not the stale placeholder.
    conn = sqlite3.connect(str(dest))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    from config.control_plane_schema import CONTROL_PLANE_TABLES
    assert set(CONTROL_PLANE_TABLES) <= tables
    leftovers = list(tmp_path.glob(".restore_candidate_*"))
    assert leftovers == []


def test_restore_latest_returns_none_when_no_backups_exist(tmp_path, monkeypatch):
    """A genuinely first-ever deployment -- nothing to restore, caller
    must fall through to starting empty rather than erroring."""
    monkeypatch.setattr(backup_mod, "ZAMBONI_METADATA_BUCKET", "s3://test-bucket/")
    monkeypatch.setattr(backup_mod, "list_keys", lambda bucket, prefix: [])

    downloads = []
    monkeypatch.setattr(backup_mod, "download_file", lambda *a: downloads.append(a))

    result = backup_mod.restore_latest(str(tmp_path / "restored.db"))

    assert result is None
    assert downloads == []
