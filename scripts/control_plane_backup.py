"""
Zamboni -- Control Plane Backup Loop

Long-running process (own systemd unit, deploy/systemd/
zamboni-control-plane-backup.service) that periodically snapshots the
SQLite control-plane DB to S3, now that it's primary storage for
stream_registry/hk_config/domain_registry/nonprod_registry/controlm_jobs,
not a disposable cache. Uses SQLite's own VACUUM INTO to produce a
transactionally-consistent snapshot even under concurrent WAL writers --
never a raw file copy of a live database file, which could capture a
torn/inconsistent state mid-write.

Retention, two tiers (see prune_backups()):
  - hourly: keep the newest backup per hour, for the most recent
    control_plane_backup_hourly_retention_hours (default 24)
  - daily:  keep the newest backup per day, for the most recent
    control_plane_backup_daily_retention_days (default 30)
  - anything older than the daily window is deleted. No EBS snapshot tier
    -- deliberately ruled out, S3 backups are the only durability layer.

Usage:
    python scripts/control_plane_backup.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.platform_settings import get_settings  # noqa: E402
from config.settings import ZAMBONI_CONTROL_PLANE_DB, ZAMBONI_METADATA_BUCKET  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402
from engine.utils.s3_client import delete_keys, download_file, list_keys, parse_s3_uri, upload_file  # noqa: E402

log = get_logger(__name__)

_MIN_TICK_SECONDS = 15
_PREFIX = "control-plane-backups/"
_KEY_TS_RE = re.compile(r"zamboni_control_(\d{8}T\d{6}Z)\.db$")


class BackupValidationError(RuntimeError):
    """
    Raised when a downloaded backup candidate fails validation (SQLite
    integrity or expected-schema check) before it would otherwise replace
    the live control-plane DB. 2026-07-11 audit fix: restore_latest()
    used to download a backup key straight onto dest_path with zero
    validation -- a corrupted or truncated S3 object (a torn upload, an
    incomplete multipart transfer) would have overwritten a working file
    with a broken one, and a backup taken by a mismatched schema version
    could silently look fine to SQLite while missing columns/tables this
    codebase now expects. Neither is ever written to dest_path -- see
    _restore_key()'s temp-file + validate + atomic-replace sequence.
    """


def take_backup() -> str:
    """
    VACUUM INTO a temp file, upload to S3, return the key. Never a raw
    file copy of the live DB -- VACUUM INTO produces a consistent
    snapshot even mid-write under WAL, unlike copying the .db file
    directly (which could capture a torn page).
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key_name = f"zamboni_control_{ts}.db"
    bucket, prefix = parse_s3_uri(ZAMBONI_METADATA_BUCKET)
    full_key = f"{prefix}{_PREFIX}{key_name}" if prefix else f"{_PREFIX}{key_name}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = str(Path(tmp) / "zamboni_control_backup.db")
        conn = sqlite3.connect(ZAMBONI_CONTROL_PLANE_DB)
        try:
            conn.execute("VACUUM INTO ?", (tmp_path,))
        finally:
            conn.close()
        upload_file(bucket, full_key, tmp_path)

    log.info("control_plane_backup.taken", key=full_key)
    return full_key


def _parse_backup_ts(key: str) -> datetime | None:
    m = _KEY_TS_RE.search(key)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def list_backups() -> list[tuple[datetime, str]]:
    """All backup (timestamp, key) pairs under the backup prefix, newest
    first. Shared by restore_latest() and scripts/control_plane_restore.py's
    interactive "pick a backup" listing."""
    bucket, prefix = parse_s3_uri(ZAMBONI_METADATA_BUCKET)
    list_prefix = f"{prefix}{_PREFIX}" if prefix else _PREFIX
    keys = list_keys(bucket, list_prefix)
    dated = [(ts, key) for key in keys if (ts := _parse_backup_ts(key)) is not None]
    return sorted(dated, key=lambda pair: pair[0], reverse=True)


def _validate_backup_file(path: str) -> None:
    """
    Confirm a downloaded backup candidate is both structurally sound
    (SQLite's own PRAGMA integrity_check) and schema-compatible (every
    table config/control_plane_schema.py currently expects is present) --
    2026-07-11 audit fix. Raises BackupValidationError on either failure;
    never mutates path itself.
    """
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupValidationError(f"PRAGMA integrity_check failed for {path}: {row}")

            from config.control_plane_schema import CONTROL_PLANE_TABLES
            existing = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = set(CONTROL_PLANE_TABLES) - existing
            if missing:
                raise BackupValidationError(
                    f"backup at {path} is missing expected table(s): {sorted(missing)} "
                    "-- likely taken by an older/incompatible schema version"
                )
        finally:
            conn.close()
    except sqlite3.Error as e:
        # A completely non-SQLite file (a torn/truncated download, garbage
        # bytes) fails at connect()/the first query with a raw
        # sqlite3.Error before ever reaching the integrity_check row --
        # must still be treated as a rejected backup, not an unhandled
        # crash.
        raise BackupValidationError(f"backup at {path} is not a valid SQLite database: {e}") from e


def _restore_key(key: str, dest_path: str) -> None:
    """
    Download `key` to a temp file IN THE SAME DIRECTORY as dest_path
    (guarantees os.replace() below is on one filesystem, so it's atomic
    rather than a copy-then-delete that could leave a half-written file
    if interrupted), validate it, then atomically replace dest_path.
    dest_path is never touched if validation fails.
    """
    bucket, _ = parse_s3_uri(ZAMBONI_METADATA_BUCKET)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(dest.parent), prefix=".restore_candidate_", suffix=".db")
    os.close(fd)
    try:
        download_file(bucket, key, tmp_path)
        _validate_backup_file(tmp_path)
        os.replace(tmp_path, dest_path)  # atomic on POSIX same-filesystem rename
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def restore_latest(dest_path: str) -> str | None:
    """
    Download, validate, and atomically install the newest available S3
    backup at dest_path. Returns the restored key, or None if no backup
    exists yet (a genuinely first-ever deployment with nothing to restore
    from -- the caller should fall through to starting empty).

    2026-07-10 audit fix: this is the missing half of the backup story --
    take_backup()/prune_backups() existed and ran on schedule, but nothing
    in this codebase ever read a backup back. Combined with the EBS root
    volume's DeleteOnTermination=true (deploy/zamboni-cfn.yaml -- since
    2026-07-11 mitigated further by a dedicated retained volume, see
    ControlPlaneVolumeSizeGiB there), a real EC2 instance replacement
    (confirmed to have already happened once) would silently start
    the control-plane DB empty with no way back. Called automatically
    from scripts/init_control_plane_db.py when the local control-plane DB
    is detected empty AND a backup exists (see that script for the "don't
    clobber a live, non-empty DB" guard), and available directly via
    scripts/control_plane_restore.py for a manual, human-operated
    recovery.

    2026-07-11 audit fix: no longer downloads straight onto dest_path --
    see _restore_key()'s temp-file + PRAGMA integrity_check + schema
    check + atomic-replace sequence. A corrupted/truncated download or an
    incompatible-schema backup is rejected (raises BackupValidationError)
    without ever touching dest_path.
    """
    backups = list_backups()
    if not backups:
        log.info("control_plane_backup.restore_latest.no_backups_found")
        return None

    _, key = backups[0]
    _restore_key(key, dest_path)
    log.info("control_plane_backup.restore_latest.restored", key=key, dest_path=dest_path)
    return key


def prune_backups(hourly_retention_hours: int = 24, daily_retention_days: int = 30) -> int:
    """
    Keep the newest backup per hour for the most recent
    hourly_retention_hours, the newest backup per day for the most recent
    daily_retention_days, delete everything else (including anything
    older than the daily window). Returns count deleted.
    """
    bucket, prefix = parse_s3_uri(ZAMBONI_METADATA_BUCKET)
    list_prefix = f"{prefix}{_PREFIX}" if prefix else _PREFIX
    keys = list_keys(bucket, list_prefix)

    dated = [(ts, key) for key in keys if (ts := _parse_backup_ts(key)) is not None]
    if not dated:
        return 0

    now = datetime.now(UTC)
    hour_cutoff = now - timedelta(hours=hourly_retention_hours)
    day_cutoff  = now - timedelta(days=daily_retention_days)

    keep: dict[str, tuple[datetime, str]] = {}  # bucket key -> (ts, key), newest wins
    to_delete: list[str] = []

    for ts, key in dated:
        if ts >= hour_cutoff:
            bucket_key = f"h:{ts.strftime('%Y-%m-%d-%H')}"
        elif ts >= day_cutoff:
            bucket_key = f"d:{ts.strftime('%Y-%m-%d')}"
        else:
            to_delete.append(key)
            continue

        existing = keep.get(bucket_key)
        if existing is None or ts > existing[0]:
            if existing is not None:
                to_delete.append(existing[1])
            keep[bucket_key] = (ts, key)
        else:
            to_delete.append(key)

    deleted = delete_keys(bucket, to_delete)
    if deleted:
        log.info("control_plane_backup.pruned", deleted=deleted, kept=len(keep))
    return deleted


def _run_forever() -> None:
    from config.settings import ZAMBONI_LOCAL_MODE
    if ZAMBONI_LOCAL_MODE:
        # No real control-plane DB or S3 to back up in local mode -- matches
        # scripts/control_plane_sync.py's and aws_smoke_test.py's convention.
        log.info("control_plane_backup.skipped_local_mode")
        return

    last_backup_at = 0.0

    while True:
        settings = get_settings()
        interval = max(int(settings.get("control_plane_backup_interval_seconds", 300)), _MIN_TICK_SECONDS)
        hourly_hours = int(settings.get("control_plane_backup_hourly_retention_hours", 24))
        daily_days   = int(settings.get("control_plane_backup_daily_retention_days", 30))

        now = time.monotonic()
        if now - last_backup_at >= interval:
            try:
                take_backup()
                prune_backups(hourly_hours, daily_days)
            except Exception as e:
                log.error("control_plane_backup.cycle_failed", error=str(e))
            last_backup_at = now

        time.sleep(min(interval, _MIN_TICK_SECONDS * 2))


if __name__ == "__main__":
    log.info("control_plane_backup.starting")
    _run_forever()
