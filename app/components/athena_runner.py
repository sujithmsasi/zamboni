"""
Zamboni — Cached Athena Runner for Streamlit
Wraps athena_client.read_sql with Streamlit's @st.cache_data
so repeated queries within a session don't re-run.

Cache TTLs:
  - 5 min for execution_log queries (relatively fresh data)
  - 30 min for stream_registry / domain_registry (changes infrequently)
  - 1 hour for snapshot data
"""
import pandas as pd
import streamlit as st

from engine.utils.athena_client import read_sql, run_query
from engine.utils.logger import get_logger

log = get_logger(__name__)


@st.cache_data(ttl=300)  # 5 minutes
def cached_read_sql(sql: str, workgroup: str = "app") -> pd.DataFrame:
    """Run a SELECT and cache results for 5 minutes."""
    log.info("streamlit.cached_query", sql_preview=sql[:120])
    return read_sql(sql=sql, workgroup=workgroup)


@st.cache_data(ttl=1800)  # 30 minutes
def cached_read_registry(sql: str, workgroup: str = "app") -> pd.DataFrame:
    """Run a registry query — cached longer since registry changes slowly."""
    return read_sql(sql=sql, workgroup=workgroup)


@st.cache_data(ttl=3600)  # 1 hour
def cached_read_snapshot(sql: str, workgroup: str = "app") -> pd.DataFrame:
    """Run a snapshot query — cached an hour."""
    return read_sql(sql=sql, workgroup=workgroup)


def execute_write(
    sql: str,
    workgroup: str = "app",
    dry_run: bool = False,
    control_plane: bool = False,
) -> None:
    """
    Execute an INSERT / UPDATE / DELETE.
    Clears all caches so next read returns fresh data.

    control_plane=True routes the write through
    engine.core.control_plane.run_query() (the SQLite-primary store for
    stream_registry/hk_config/domain_registry/nonprod_registry/
    controlm_jobs) instead of real Athena. Required for any write against
    one of those 5 tables -- the engine now reads them from SQLite
    directly, and scripts/control_plane_sync.py's sync is one-way
    (SQLite -> Athena), so a write against real Athena here would be
    invisible to the engine, not just delayed.
    """
    if control_plane:
        from engine.core import control_plane as _control_plane_mod
        _control_plane_mod.run_query(sql=sql, workgroup=workgroup, dry_run=dry_run)
    else:
        run_query(sql=sql, workgroup=workgroup, dry_run=dry_run)
    if not dry_run:
        clear_caches()


def clear_caches() -> None:
    """Clear all cached query results."""
    cached_read_sql.clear()
    cached_read_registry.clear()
    cached_read_snapshot.clear()
    log.info("streamlit.caches_cleared")
