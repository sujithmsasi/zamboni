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

_conns: dict[str, sqlite3.Connection] = {}


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """
    Return (or create) the SQLite connection for db_path.
    db_path defaults to ZAMBONI_LOCAL_DB (the dev/demo fixture) for
    backward compatibility -- pass an explicit path (e.g.
    config.settings.ZAMBONI_CONTROL_PLANE_DB) to get a distinct,
    independent connection, such as the control-plane primary DB. One
    connection is cached per resolved path, not a single global, so the
    two files never collide within the same process.
    """
    if db_path is None:
        from config.settings import ZAMBONI_LOCAL_DB
        db_path = ZAMBONI_LOCAL_DB
    if db_path not in _conns:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent read performance
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # synchronous=NORMAL + a busy_timeout are cheap everywhere and matter
        # once a db_path is used as real persistent primary storage (the
        # control-plane DB), not just a disposable dev/demo fixture.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conns[db_path] = conn
        log.info("local_db.connected", path=db_path)
    return _conns[db_path]


def read_sql_local(sql: str, db_path: str | None = None) -> pd.DataFrame:
    """
    Execute a SELECT against the local SQLite DB.
    Translates common Athena-isms to SQLite equivalents automatically.
    Returns an empty DataFrame on error.
    """
    sql = _translate(sql)
    try:
        conn = get_connection(db_path)
        df   = pd.read_sql_query(sql, conn)
        log.debug("local_db.read_sql", rows=len(df), sql=sql[:120])
        return df
    except Exception as e:
        log.error("local_db.read_sql_failed", error=str(e), sql=sql[:200])
        return pd.DataFrame()


def run_query_local(sql: str, db_path: str | None = None) -> str | None:
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
        conn = get_connection(db_path)
        conn.execute(sql)
        conn.commit()
        qid = "local-" + _fake_id()
        log.debug("local_db.run_query", query_id=qid, sql=sql[:120])
        return qid
    except Exception as e:
        log.error("local_db.run_query_failed", error=str(e), sql=sql[:200])
        return None


class LocalDbReadError(RuntimeError):
    """Raised by read_sql_strict() when the underlying SQL read fails.
    2026-07-11 audit fix -- unlike read_sql_local() (which returns an
    empty DataFrame on any error, a deliberate convenience for local/demo
    UI simulation where a broken query should just render 'no rows'
    rather than crash the page), production control-plane reads must not
    be able to silently look identical to 'zero rows matched' when the
    real cause is a disk-full/locked-file/malformed-query failure."""


class LocalDbWriteError(RuntimeError):
    """Raised by run_query_strict() on a write failure, or (when
    expect_rowcount=True) on an UPDATE/DELETE that matched zero rows.
    2026-07-11 audit fix -- companion to LocalDbReadError for the write
    side; used by the production control-plane path
    (engine/core/control_plane.py), not the local/demo simulation path."""


def read_sql_strict(sql: str, db_path: str | None = None) -> pd.DataFrame:
    """
    Strict counterpart to read_sql_local(): raises LocalDbReadError on any
    read failure instead of returning an empty DataFrame. A query that
    runs successfully and genuinely matches zero rows is NOT an error --
    only a real exception (bad SQL, missing table, disk/IO failure)
    raises here, so "no results" and "couldn't read" can never be
    confused by a caller.
    """
    translated = _translate(sql)
    try:
        conn = get_connection(db_path)
        df = pd.read_sql_query(translated, conn)
        log.debug("local_db.read_sql_strict", rows=len(df), sql=translated[:120])
        return df
    except Exception as e:
        log.error("local_db.read_sql_strict_failed", error=str(e), sql=translated[:200])
        raise LocalDbReadError(f"strict SQLite read failed: {e}") from e


def run_query_strict(sql: str, db_path: str | None = None, expect_rowcount: bool = False) -> str:
    """
    Strict counterpart to run_query_local(): raises LocalDbWriteError on
    any write failure instead of returning None. When expect_rowcount is
    True (used by control_plane.py::update_row(), which always targets
    exactly one row by a primary key the caller already resolved), an
    UPDATE/DELETE that matched zero rows also raises -- that shape of
    call has no legitimate "nothing matched" outcome, so silently
    reporting success would hide a stale key or a row deleted out from
    under the caller.
    """
    translated = _translate(sql)
    try:
        conn = get_connection(db_path)
        cur = conn.execute(translated)
        conn.commit()
    except Exception as e:
        log.error("local_db.run_query_strict_failed", error=str(e), sql=translated[:200])
        raise LocalDbWriteError(f"strict SQLite write failed: {e}") from e

    if expect_rowcount and cur.rowcount == 0:
        raise LocalDbWriteError(
            f"write affected 0 rows (expected at least 1 -- the target row may not "
            f"exist or was already changed): {translated[:200]}"
        )

    qid = "local-" + _fake_id()
    log.debug("local_db.run_query_strict", query_id=qid, sql=translated[:120])
    return qid


def translate_athena_sql(sql: str) -> str:
    """Public wrapper around _translate() for callers outside this module
    (e.g. engine.core.athena_cache) that need the same Athena-to-SQLite
    rewriting without duplicating it."""
    return _translate(sql)


def create_tables(ddl_map: dict[str, str], db_path: str | None = None) -> None:
    """
    Create tables from a dict of {table_name: sqlite_ddl}.
    Called by the seed script and by scripts/init_control_plane_db.py.
    """
    conn = get_connection(db_path)
    for table_name, ddl in ddl_map.items():
        try:
            conn.execute(ddl)
            conn.commit()
            log.info("local_db.table_created", table=table_name)
        except Exception as e:
            log.warning("local_db.table_create_failed",
                        table=table_name, error=str(e))


def insert_rows(table: str, rows: list[dict], db_path: str | None = None) -> int:
    """Bulk insert rows into a local table. Returns count inserted."""
    if not rows:
        return 0
    conn   = get_connection(db_path)
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


def reset_db(db_path: str | None = None) -> None:
    """Drop all Zamboni tables and recreate from scratch. Used by seed script."""
    if db_path is None:
        from config.settings import ZAMBONI_LOCAL_DB
        db_path = ZAMBONI_LOCAL_DB
    conn = _conns.pop(db_path, None)
    if conn:
        conn.close()
    path = Path(db_path)
    if path.exists():
        path.unlink()
        log.info("local_db.reset", path=str(path))


# ── SQL translation ───────────────────────────────────────────────────────────

def _translate_date_diff(sql: str) -> str:
    """
    Replace DATE_DIFF('unit', a, b) with SQLite-compatible CAST/julianday.
    Uses char-by-char scan to handle nested parens correctly.
    """
    result = []
    i = 0
    upper = sql.upper()
    while i < len(sql):
        # Look for DATE_DIFF (case-insensitive)
        if upper[i:i+9] == "DATE_DIFF" and (i == 0 or not sql[i-1].isalnum()):
            j = i + 9
            # Skip whitespace to opening paren
            while j < len(sql) and sql[j] in " \t":
                    j += 1
            if j < len(sql) and sql[j] == "(":
                j += 1  # skip (
                # Parse unit 'day' or 'hour'
                while j < len(sql) and sql[j] in " \t":
                    j += 1
                if sql[j] == "'":
                    j += 1
                    unit_start = j
                    while j < len(sql) and sql[j] != "'":
                        j += 1
                    unit = sql[unit_start:j].lower()
                    j += 1  # skip closing '
                else:
                    unit = ""
                # Skip comma
                while j < len(sql) and sql[j] in " \t,":
                    j += 1
                # Parse arg_a — scan until comma at depth 0
                depth = 0
                arg_start = j
                while j < len(sql):
                    if sql[j] == "(":
                        depth += 1
                    elif sql[j] == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    elif sql[j] == "," and depth == 0:
                        break
                    j += 1
                arg_a = sql[arg_start:j].strip()
                # Skip comma
                while j < len(sql) and sql[j] in " \t,":
                    j += 1
                # Parse arg_b — scan until ) at depth 0
                depth = 0
                arg_start = j
                while j < len(sql):
                    if sql[j] == "(":
                        depth += 1
                    elif sql[j] == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    j += 1
                arg_b = sql[arg_start:j].strip()
                j += 1  # skip closing )

                if unit == "day":
                    result.append(
                        f"CAST((julianday({arg_b}) - julianday({arg_a})) AS INTEGER)"
                    )
                elif unit == "hour":
                    result.append(
                        f"CAST(((julianday({arg_b}) - julianday({arg_a})) * 24) AS INTEGER)"
                    )
                else:
                    result.append(sql[i:j])  # unknown unit, pass through
                i = j
                continue
        result.append(sql[i])
        i += 1
    return "".join(result)


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
    # DATE_DIFF translation — scan-based to handle nested parens in args
    sql = _translate_date_diff(sql)
    # DATE_TRUNC('month'/'week'/'day', col) -> SQLite strftime equivalent
    def _replace_date_trunc(m: re.Match) -> str:
        unit = m.group(1).lower()
        col  = m.group(2).strip()
        if unit == "month":
            return f"strftime('%Y-%m-01', {col})"
        if unit == "week":
            return f"date({col}, 'weekday 0', '-6 days')"
        if unit == "year":
            return f"strftime('%Y-01-01', {col})"
        return f"date({col})"  # day default
    sql = re.sub(
        r"DATE_TRUNC\s*\(\s*'(\w+)'\s*,\s*([^)]+)\)",
        _replace_date_trunc,
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
