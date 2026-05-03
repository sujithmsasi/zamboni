"""
Zamboni -- Cascading Table Selector Component
Shared domain → Glue database → table selection used by:
  - Table Registration (new table onboarding)
  - Dry Run Viewer (table to simulate)
  - Health Dashboard (table to health-check)
  - Live Activity (table to replay)

Keeps manual FQN fallback for power users.
All Glue calls are cached per session to avoid repeated API calls.
"""
from __future__ import annotations

import streamlit as st

from engine.utils.logger import get_logger

log = get_logger(__name__)


@st.cache_data(ttl=300, show_spinner=False)
def _get_domains() -> list[str]:
    """Return all active domain names from domain_registry."""
    try:
        from app.components.athena_runner import cached_read_registry
        from config.settings import DOMAIN_REGISTRY_TABLE
        df = cached_read_registry(
            f"SELECT DISTINCT domain FROM {DOMAIN_REGISTRY_TABLE} "
            "WHERE environment = 'prod' ORDER BY domain"
        )
        if df.empty:
            return []
        return df["domain"].tolist()
    except Exception as e:
        log.warning("table_selector.get_domains_failed", error=str(e))
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _get_databases_for_domain(domain: str) -> list[str]:
    """Return Glue database names associated with a domain."""
    try:
        from engine.utils.glue_client import list_databases
        all_dbs = list_databases()
        # Heuristic: databases that start with or contain the domain name
        matching = [
            db for db in all_dbs
            if domain.lower() in db.lower()
        ]
        return sorted(matching) if matching else sorted(all_dbs)
    except Exception as e:
        log.warning("table_selector.get_databases_failed",
                    domain=domain, error=str(e))
        return []


@st.cache_data(ttl=120, show_spinner=False)
def _get_iceberg_tables(database: str) -> list[str]:
    """Return Iceberg table names in a Glue database."""
    try:
        from engine.utils.glue_client import get_tables
        tables = get_tables(database, table_format="iceberg")
        return sorted([t["Name"] for t in tables])
    except Exception as e:
        log.warning("table_selector.get_tables_failed",
                    database=database, error=str(e))
        return []


def render(
    key_prefix:        str  = "table_sel",
    show_manual:       bool = True,
    label:             str  = "Select Table",
    include_all_envs:  bool = False,
) -> tuple[str | None, str | None, str | None]:
    """
    Render cascading domain → database → table selector.

    Returns:
        (domain, database, table_fqn) — any can be None if not yet selected.
        table_fqn is the full glue_catalog.<db>.<table> string.

    Args:
        key_prefix:   Unique prefix for Streamlit widget keys
        show_manual:  Whether to show a manual FQN input fallback
        label:        Section label shown above the cascade
    """
    st.markdown(f"**{label}**")

    # ── Manual FQN toggle ────────────────────────────────────────────────────
    use_manual = False
    if show_manual:
        use_manual = st.checkbox(
            "Enter table FQN manually (advanced)",
            key=f"{key_prefix}_manual_toggle",
        )

    if use_manual:
        fqn = st.text_input(
            "Table FQN",
            placeholder="glue_catalog.finance_db.finance_staging",
            key=f"{key_prefix}_manual_fqn",
        ).strip()
        if fqn and fqn.count(".") == 2:
            parts = fqn.split(".")
            return None, parts[1], fqn
        if fqn:
            st.caption("Format: glue_catalog.<database>.<table>")
        return None, None, None

    # ── Cascading selector ───────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)

    with col1:
        domains = _get_domains()
        if not domains:
            st.caption("No domains found — check Athena connectivity")
            return None, None, None
        domain = st.selectbox(
            "Domain",
            ["-- select --"] + domains,
            key=f"{key_prefix}_domain",
        )

    if domain == "-- select --":
        return None, None, None

    with col2:
        databases = _get_databases_for_domain(domain)
        if not databases:
            st.caption("No databases found for this domain")
            return domain, None, None
        database = st.selectbox(
            "Glue Database",
            ["-- select --"] + databases,
            key=f"{key_prefix}_database",
        )

    if database == "-- select --":
        return domain, None, None

    with col3:
        tables = _get_iceberg_tables(database)
        if not tables:
            st.caption("No Iceberg tables found in this database")
            return domain, database, None
        table = st.selectbox(
            "Table",
            ["-- select --"] + tables,
            key=f"{key_prefix}_table",
        )

    if table == "-- select --":
        return domain, database, None

    table_fqn = f"glue_catalog.{database}.{table}"
    st.caption(f"FQN: `{table_fqn}`")
    return domain, database, table_fqn
