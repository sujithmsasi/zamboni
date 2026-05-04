"""
Zamboni -- Local SQLite Database
Provides a drop-in replacement for Athena when running in LOCAL_MODE.

All read_sql() and run_query() calls in athena_client.py are routed
here when ZAMBONI_LOCAL_MODE=true. The rest of the codebase is unchanged.

SQLite limitations vs Athena:
  - No Iceberg-specific syntax (OPTIMIZE, expire_snapshots etc.)
    These are engine operations -- the UI never issues them directly.
  - No TIMESTAMP type -- stored as TEXT in ISO format.
  - No INTERVAL syntax -- date arithmetic uses SQLite functions.
  - CURRENT_DATE works in SQLite identically to Athena.

The local DB is a single file: zamboni_local.db (configurable via .env).
Run `python scripts/seed_local_db.py` to create and populate it.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from engine.utils.logger import get_logger

log = get_logger(__name__)

_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    """Return (or create) the singleton SQLite connection."""
    global _conn
    if _conn is None:
        from config.settings import ZAMBONI_LOCAL_DB
        db_path = ZAMBONI_LOCAL_DB
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent read performance
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        log.info("local_db.connected", path=db_path)
    return _conn


def read_sql_local(sql: str) -> pd.DataFrame:
    """
    Execute a SELECT against the local SQLite DB.
    Translates common Athena-isms to SQLite equivalents automatically.
    Returns an empty DataFrame on error.
    """
    sql = _translate(sql)
    try:
        conn = get_connection()
        df   = pd.read_sql_query(sql, conn)
        log.debug("local_db.read_sql", rows=len(df), sql=sql[:120])
        return df
    except Exception as e:
        log.error("local_db.read_sql_failed", error=str(e), sql=sql[:200])
        return pd.DataFrame()


def run_query_local(sql: str) -> str | None:
    """
    Execute an INSERT/UPDATE/DELETE against the local SQLite DB.
    Returns a fake query_id string (for compatibility with callers).
    Silently skips Athena/Iceberg-specific DDL that SQLite cannot run.
    """
    # Skip Iceberg engine operations -- UI never issues these directly
    skip_patterns = [
        r"OPTIMIZE\s+TABLE",
        r"ALTER\s+TABLE.*EXECUTE\s+expire_snapshots",
        r"ALTER\s+TABLE.*EXECUTE\s+remove_orphan_files",
        r"ALTER\s+TABLE.*SET\s+TBLPROPERTIES",
        r"CALL\s+system\.add_files",
        r"CREATE\s+TABLE.*LOCATION.*TBLPROPERTIES",
    ]
    for pattern in skip_patterns:
        if re.search(pattern, sql, re.IGNORECASE):
            log.debug("local_db.skipped_iceberg_sql", sql=sql[:80])
            return "local-noop-" + _fake_id()

    sql = _translate(sql)
    try:
        conn = get_connection()
        conn.execute(sql)
        conn.commit()
        qid = "local-" + _fake_id()
        log.debug("local_db.run_query", query_id=qid, sql=sql[:120])
        return qid
    except Exception as e:
        log.error("local_db.run_query_failed", error=str(e), sql=sql[:200])
        return None


def create_tables(ddl_map: dict[str, str]) -> None:
    """
    Create tables from a dict of {table_name: sqlite_ddl}.
    Called by the seed script.
    """
    conn = get_connection()
    for table_name, ddl in ddl_map.items():
        try:
            conn.execute(ddl)
            conn.commit()
            log.info("local_db.table_created", table=table_name)
        except Exception as e:
            log.warning("local_db.table_create_failed",
                        table=table_name, error=str(e))


def insert_rows(table: str, rows: list[dict]) -> int:
    """Bulk insert rows into a local table. Returns count inserted."""
    if not rows:
        return 0
    conn   = get_connection()
    cols   = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_list     = ", ".join(cols)
    sql    = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    values = [[r.get(c) for c in cols] for r in rows]
    try:
        conn.executemany(sql, values)
        conn.commit()
        return len(values)
    except Exception as e:
        log.error("local_db.insert_failed", table=table, error=str(e))
        return 0


def reset_db() -> None:
    """Drop all Zamboni tables and recreate from scratch. Used by seed script."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
    from config.settings import ZAMBONI_LOCAL_DB
    db_path = Path(ZAMBONI_LOCAL_DB)
    if db_path.exists():
        db_path.unlink()
        log.info("local_db.reset", path=str(db_path))


# ── SQL translation ───────────────────────────────────────────────────────────

def _translate(sql: str) -> str:
    """
    Translate Athena SQL to SQLite-compatible SQL.
    Handles the most common patterns used in Zamboni UI queries.
    """
    # Strip Athena catalog prefix ONLY in SQL structural positions
    # (FROM, JOIN, UPDATE table, INSERT INTO table) — never inside string literals.
    #
    # Strategy: strip quoted form "glue_catalog"."db"."table" (always structural)
    sql = re.sub(
        r'"glue_catalog"\."[\w]+"\."([\w]+)"',
        r'\1',
        sql,
    )
    # Strip unquoted form only when NOT preceded by a single quote
    # (i.e. not inside a VALUES string literal)
    # Negative lookbehind for apostrophe covers: WHERE x = 'glue_catalog.db.t'
    sql = re.sub(
        r"(?<!')glue_catalog\.([\w]+)\.([\w]+)(?!')",
        r'\2',
        sql,
    )
    # INTERVAL syntax: INTERVAL '7' DAY -> 7 (SQLite uses numeric offsets)
    sql = re.sub(
        r"INTERVAL\s+'(\d+)'\s+DAY",
        r"\1",
        sql, flags=re.IGNORECASE,
    )
    # DATE_DIFF('day', a, b) -> (julianday(b) - julianday(a))
    sql = re.sub(
        r"DATE_DIFF\s*\(\s*'day'\s*,\s*([^,]+),\s*([^)]+)\)",
        r"CAST(julianday(\2) - julianday(\1) AS INTEGER)",
        sql, flags=re.IGNORECASE,
    )
    # DATE_DIFF('hour', a, b)
    sql = re.sub(
        r"DATE_DIFF\s*\(\s*'hour'\s*,\s*([^,]+),\s*([^)]+)\)",
        r"CAST((julianday(\2) - julianday(\1)) * 24 AS INTEGER)",
        sql, flags=re.IGNORECASE,
    )
    # NOW() -> datetime('now')
    sql = re.sub(r'\bNOW\(\)', "datetime('now')", sql, flags=re.IGNORECASE)
    # CURRENT_DATE -> date('now')
    sql = re.sub(r'\bCURRENT_DATE\b', "date('now')", sql, flags=re.IGNORECASE)
    # CURRENT_TIMESTAMP -> datetime('now')
    sql = re.sub(r'\bCURRENT_TIMESTAMP\b', "datetime('now')", sql, flags=re.IGNORECASE)
    # TIMESTAMP 'yyyy-mm-dd hh:mm:ss' -> 'yyyy-mm-dd hh:mm:ss'
    sql = re.sub(r"\bTIMESTAMP\s+'([^']+)'", r"'\1'", sql, flags=re.IGNORECASE)
    # DATE 'yyyy-mm-dd' -> 'yyyy-mm-dd'
    sql = re.sub(r"\bDATE\s+'([^']+)'", r"'\1'", sql, flags=re.IGNORECASE)
    # ROUND(x, n) is fine in SQLite
    # NULLS FIRST / NULLS LAST -- not supported in old SQLite, strip safely
    sql = re.sub(r'\bNULLS\s+(FIRST|LAST)\b', '', sql, flags=re.IGNORECASE)
    # true/false literals -> 1/0
    sql = re.sub(r'\btrue\b',  '1', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bfalse\b', '0', sql, flags=re.IGNORECASE)
    # LIKE 'FAIL%' is fine in SQLite
    return sql


def _fake_id() -> str:
    import uuid
    return str(uuid.uuid4())[:8]
