"""
Zamboni -- Control Plane DB init/migrate

Idempotent: creates the control-plane SQLite tables if missing, and applies
any pending column migrations if not. Safe to run on every deploy (invoked
from deploy/scripts/after_install.sh) -- CREATE TABLE IF NOT EXISTS and a
guarded ALTER TABLE loop, both no-ops once already applied.

Deliberately targets ONLY config.settings.ZAMBONI_CONTROL_PLANE_DB, never
ZAMBONI_LOCAL_DB (scripts/seed_local_db.py owns that file and seeds demo
data into it; this script never touches it and never seeds any data --
the control-plane DB starts genuinely empty, populated only by real UI/API
writes).

Usage:
    python scripts/init_control_plane_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.control_plane_schema import (  # noqa: E402
    CONTROL_PLANE_MIGRATIONS,
    CONTROL_PLANE_TABLES,
)
from config.settings import (  # noqa: E402
    ZAMBONI_CONTROL_PLANE_DB,
    ZAMBONI_CONTROL_PLANE_FIRST_INSTALL,
    ZAMBONI_LOCAL_MODE,
)
from engine.utils.local_db import create_tables, get_connection  # noqa: E402
from engine.utils.logger import get_logger  # noqa: E402

log = get_logger(__name__)


class ControlPlaneEmptyAndNoBackupError(RuntimeError):
    """
    Raised when the control-plane DB is empty, no S3 backup was available
    to restore it, and ZAMBONI_CONTROL_PLANE_FIRST_INSTALL wasn't set to
    acknowledge this as a genuine first-ever deployment (2026-07-11 audit
    fix). Deliberately fails this script (and therefore the CodeDeploy
    AfterInstall hook that calls it, via after_install.sh's `set -e`)
    rather than silently starting a real environment with a blank
    production control plane.
    """


class ControlPlaneSchemaVerificationError(RuntimeError):
    """
    Raised when, after table creation and migration, the control-plane DB
    is still missing an expected table or column (2026-07-11 audit fix).
    Fails this script (and the CodeDeploy AfterInstall hook) rather than
    letting the deploy proceed against a schema the rest of the codebase
    assumes is complete.
    """


def _existing_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _apply_migrations(conn) -> int:
    """
    Apply pending ALTER TABLE ADD COLUMN migrations, as one transaction.

    2026-07-11 audit fix: the previous version wrapped every ALTER in a
    bare `except Exception: pass`, assuming any failure meant "column
    already exists" -- but that same catch-all would just as easily
    swallow a genuine failure (a locked file, a disk-full write, a real
    syntax error) with zero visibility. Now checks PRAGMA table_info(table)
    first to determine whether the column genuinely already exists (the
    ONLY case silently skipped) and lets any other failure raise for
    real. Runs as a single transaction (SQLite DDL is fully transactional)
    so a failure partway through rolls back every migration in this run,
    leaving the schema exactly as it was rather than partially migrated.
    """
    migrated = 0
    conn.execute("BEGIN")
    try:
        for table, col, sqlite_type in CONTROL_PLANE_MIGRATIONS:
            if col in _existing_columns(conn, table):
                continue  # already migrated -- genuinely nothing to do
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sqlite_type}")
            migrated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return migrated


def _verify_schema(conn) -> None:
    """
    2026-07-11 audit fix: confirm every control-plane table exists with
    every column CONTROL_PLANE_MIGRATIONS expects before letting the
    deploy proceed -- catches a create/migrate step that silently didn't
    fully apply rather than letting the rest of the app run against an
    incomplete schema.
    """
    existing_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    missing_tables = set(CONTROL_PLANE_TABLES) - existing_tables
    if missing_tables:
        raise ControlPlaneSchemaVerificationError(
            f"control-plane DB is missing expected table(s): {sorted(missing_tables)}"
        )

    for table, col, _sqlite_type in CONTROL_PLANE_MIGRATIONS:
        if col not in _existing_columns(conn, table):
            raise ControlPlaneSchemaVerificationError(
                f"control-plane table '{table}' is missing expected column '{col}'"
            )


def _is_genuinely_empty(db_path: str) -> bool:
    """
    True only if every one of the 5 control-plane tables has zero rows.

    2026-07-10 audit fix (part of the control-plane recovery gap): a
    normal CodeDeploy redeploy never wipes /data/zamboni (it lives outside
    /opt/zamboni, which is the only thing CodeDeploy replaces) -- only a
    real EC2 instance replacement does, since the root EBS volume is
    DeleteOnTermination=true (deploy/zamboni-cfn.yaml). So an existing
    instance's tables already have real rows and this returns False before
    an auto-restore is ever attempted; a freshly-replaced instance's tables
    are all genuinely zero, which is exactly the one case auto-restore is
    safe and correct for. Any read failure is treated conservatively as
    "not empty" so an automatic restore is never attempted over a table
    this check couldn't fully verify.
    """
    conn = get_connection(db_path)
    for table_name in CONTROL_PLANE_TABLES:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        except Exception as e:
            log.warning("init_control_plane_db.empty_check_failed", table=table_name, error=str(e))
            return False
        if count > 0:
            return False
    return True


def _attempt_restore_on_empty(db_path: str) -> None:
    """
    Auto-heal path for the control-plane-recovery gap: every table is
    genuinely empty right after create_tables(), which is either a
    first-ever deployment (no S3 backup exists yet -- correct to stay
    empty) or a real instance replacement (a backup should exist). Closes
    the cached SQLite connection before overwriting the file out from
    under it -- a live sqlite3.Connection doesn't notice its backing file
    being replaced on disk -- and lets the next get_connection() call in
    main() re-open fresh against the restored (or still-empty) file.
    """
    import engine.utils.local_db as local_db

    try:
        from scripts.control_plane_backup import restore_latest
    except Exception as e:
        log.warning("init_control_plane_db.restore_unavailable", error=str(e))
        return

    conn = local_db._conns.pop(db_path, None)
    if conn is not None:
        conn.close()

    try:
        restored_key = restore_latest(db_path)
    except Exception as e:
        log.error("init_control_plane_db.restore_failed", error=str(e))
        # Leave a working (if empty) DB behind rather than a half-written
        # file from a failed download.
        create_tables(CONTROL_PLANE_TABLES, db_path=db_path)
        return

    if restored_key is None:
        print(
            "Control-plane DB is empty and no S3 backup was found -- "
            "starting empty (expected on a genuinely first-ever deployment)."
        )
        return

    print(f"Control-plane DB was empty -- auto-restored from backup: {restored_key}")
    log.info("init_control_plane_db.auto_restored", key=restored_key)


def main() -> None:
    log.info("init_control_plane_db.starting", path=ZAMBONI_CONTROL_PLANE_DB)
    create_tables(CONTROL_PLANE_TABLES, db_path=ZAMBONI_CONTROL_PLANE_DB)

    if not ZAMBONI_LOCAL_MODE and _is_genuinely_empty(ZAMBONI_CONTROL_PLANE_DB):
        _attempt_restore_on_empty(ZAMBONI_CONTROL_PLANE_DB)

        # 2026-07-11 audit fix: if the restore attempt didn't fill it in
        # (no backup existed, or restore failed), this is either a
        # genuine first-ever deployment (fine, but must be acknowledged
        # explicitly) or a replacement instance that just silently lost
        # its entire production control plane (must NOT proceed quietly).
        if _is_genuinely_empty(ZAMBONI_CONTROL_PLANE_DB):
            if not ZAMBONI_CONTROL_PLANE_FIRST_INSTALL:
                raise ControlPlaneEmptyAndNoBackupError(
                    "Control-plane DB is empty and no S3 backup was available to restore it. "
                    "Refusing to start with a silently-empty production control plane -- every "
                    "registered domain/table/policy/gate/Control-M mapping would otherwise "
                    "vanish with no warning. If this is genuinely a first-ever deployment (no "
                    "prior data exists anywhere), set ZAMBONI_CONTROL_PLANE_FIRST_INSTALL=true "
                    "in .env and redeploy to acknowledge that and proceed."
                )
            log.warning(
                "init_control_plane_db.first_install_acknowledged_starting_empty",
                path=ZAMBONI_CONTROL_PLANE_DB,
            )
            print(
                "ZAMBONI_CONTROL_PLANE_FIRST_INSTALL=true -- starting with an empty "
                "control-plane DB as an acknowledged first-ever deployment."
            )

    conn = get_connection(ZAMBONI_CONTROL_PLANE_DB)
    migrated = _apply_migrations(conn)
    _verify_schema(conn)

    print(f"Control-plane DB ready: {ZAMBONI_CONTROL_PLANE_DB}")
    print(f"  {len(CONTROL_PLANE_TABLES)} table(s), {migrated} new migration(s) applied, schema verified")


if __name__ == "__main__":
    main()
