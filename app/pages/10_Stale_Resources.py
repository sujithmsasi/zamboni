"""
Zamboni — Stale Resources
Identifies:
  - Stale tables (no query/write activity beyond threshold)
  - Orphaned S3 locations (S3 data exists but no Glue table)
  - Large unmanaged tables (not registered in Zamboni)
  - Zero-row tables wasting storage
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from app.components.athena_runner import cached_read_sql
from app.components.auth import check_login
from app.components.filters import domain_filter, environment_filter
from app.components.header import render as render_header
from app.components.kpi_cards import render_kpi_row
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import lifecycle as lifecycle_badge
from config.settings import (
    EXECUTION_LOG_TABLE,
    NONPROD_REGISTRY_TABLE,
    STREAM_REGISTRY_TABLE,
)
from engine.core.audit import AuditAction, AuditEvent, audit

st.set_page_config(page_title="Zamboni — Stale Resources", page_icon="🔎", layout="wide")
check_login()
render_sidebar()
render_header(page_title="Stale Resources", page_icon="🗑️")

tab1, tab2, tab3, tab4 = st.tabs([
    "🕰️ Stale Tables",
    "📦 Unregistered Tables",
    "🪣 Orphaned S3 Locations",
    "📭 Zero-Row Tables",
])

# ── Tab 1: Stale Tables ───────────────────────────────────────────────────────
with tab1:
    st.markdown("Tables registered in Zamboni with no successful HK or user activity beyond threshold.")

    col1, col2, col3 = st.columns(3)
    with col1:
        sel_domain = domain_filter(key="sr_domain")
    with col2:
        sel_env    = environment_filter(key="sr_env")
    with col3:
        stale_days = st.number_input("Inactive for more than (days)", value=30, min_value=1, key="sr_days")

    # Prod stale — registered and enabled but no successful HK in N days
    stale_sql = f"""
        SELECT
            r.table_fqn,
            r.domain,
            r.layer,
            r.tier,
            r.environment,
            r.hk_enabled,
            MAX(l.completed_at)     AS last_successful_hk,
            DATE_DIFF('day', MAX(l.completed_at), NOW()) AS days_since_hk
        FROM {STREAM_REGISTRY_TABLE} r
        LEFT JOIN {EXECUTION_LOG_TABLE} l
            ON  r.table_fqn = l.table_fqn
            AND l.status    = 'SUCCESS'
        WHERE r.environment = '{sel_env}'
          AND r.table_format = 'iceberg'
          {("AND r.domain = '" + str(sel_domain) + "'") if sel_domain and isinstance(sel_domain, str) else ""}
        GROUP BY r.table_fqn, r.domain, r.layer, r.tier, r.environment, r.hk_enabled
        HAVING MAX(l.completed_at) IS NULL
            OR DATE_DIFF('day', MAX(l.completed_at), NOW()) > {stale_days}
        ORDER BY days_since_hk DESC NULLS FIRST
        LIMIT 200
    """

    with st.spinner("Scanning for stale tables..."):
        try:
            df = cached_read_sql(stale_sql)
            if df.empty:
                st.success(f"✅ No stale tables found (threshold: {stale_days} days).")
            else:
                st.warning(f"⚠️ {len(df)} tables have had no successful HK in {stale_days}+ days")

                # KPIs
                never_hk    = df["last_successful_hk"].isna().sum()
                hk_disabled = (not df["hk_enabled"]).sum()
                render_kpi_row([
                    {"label": "Stale Tables",       "value": str(len(df))},
                    {"label": "Never Housekept",     "value": str(never_hk)},
                    {"label": "HK Disabled",         "value": str(hk_disabled),
                     "help": "These tables have HK switched off"},
                ])

                st.divider()
                df["hk_enabled"]      = df["hk_enabled"].apply(lambda x: "✅" if x else "❌")
                df["days_since_hk"]   = df["days_since_hk"].apply(
                    lambda x: f"{int(x)}d" if pd.notna(x) else "Never"
                )
                st.dataframe(df, use_container_width=True, hide_index=True, height=420)

                csv = df.to_csv(index=False)
                st.download_button("⬇️ Export", csv, "stale_tables.csv", "text/csv")
        except Exception as e:
            st.error(f"Query failed: {e}")

    # NonProd stale (from nonprod_registry)
    st.divider()
    st.subheader("Non-Prod Stale Tables")
    np_stale_sql = f"""
        SELECT
            table_fqn, domain, environment,
            lifecycle_state, days_since_activity,
            last_query_at, last_write_at,
            created_at, is_backup_pattern
        FROM {NONPROD_REGISTRY_TABLE}
        WHERE lifecycle_state IN ('STALE_CANDIDATE', 'GREENZONE', 'PENDING_DROP')
          {("AND domain = '" + str(sel_domain) + "'") if sel_domain and isinstance(sel_domain, str) else ""}
        ORDER BY lifecycle_state, days_since_activity DESC
        LIMIT 100
    """
    try:
        np_df = cached_read_sql(np_stale_sql)
        if np_df.empty:
            st.success("✅ No stale non-prod tables.")
        else:
            np_df["lifecycle_state"]  = np_df["lifecycle_state"].apply(lifecycle_badge)
            np_df["is_backup_pattern"]= np_df["is_backup_pattern"].apply(
                lambda x: "🗂️ Backup" if int(x or 0) else ""
            )
            st.dataframe(np_df, use_container_width=True, hide_index=True, height=300)
    except Exception as e:
        st.error(f"Non-prod query failed: {e}")


# ── Tab 2: Unregistered Tables ────────────────────────────────────────────────
with tab2:
    st.markdown("Iceberg tables that exist in the Glue catalog but are **not registered** in Zamboni.")
    st.info("These tables have no HK policy — they may be accumulating snapshots and orphan files unmanaged.")

    @st.cache_data(ttl=120, show_spinner=False)
    def _known_dbs() -> list[str]:
        try:
            from app.components.athena_runner import cached_read_registry
            from config.settings import STREAM_REGISTRY_TABLE
            df = cached_read_registry(
                f"SELECT DISTINCT database_name FROM {STREAM_REGISTRY_TABLE} "
                "WHERE database_name IS NOT NULL AND database_name != '' "
                "ORDER BY database_name"
            )
            return df["database_name"].tolist() if not df.empty else []
        except Exception:
            return []

    _dbs = _known_dbs()
    _col_db, _col_scan = st.columns([4, 1])
    with _col_db:
        if _dbs:
            _db_sel = st.selectbox(
                "Glue Database to scan",
                ["-- select --"] + _dbs,
                key="sr_unrg_db",
                help="Select a database to scan for unregistered Iceberg tables.",
            )
            sel_db = "" if _db_sel == "-- select --" else _db_sel
        else:
            sel_db = st.text_input(
                "Glue Database to scan",
                placeholder="finance_staging_db",
                key="sr_unrg_db",
                help="Enter the Glue database name manually.",
            )
    with _col_scan:
        st.write("")  # spacer for alignment
        _scan_clicked = st.button(
            "🔍 Scan",
            key="sr_scan",
            disabled=not bool(sel_db),
            type="primary",
        )

    if sel_db and _scan_clicked:
        with st.spinner(f"Scanning `{sel_db}` for unregistered tables..."):
            try:
                from engine.utils.glue_client import get_tables, is_iceberg_table
                glue_tables = get_tables(sel_db)
                iceberg_tables = [
                    f"glue_catalog.{sel_db}.{t['Name']}"
                    for t in glue_tables
                    if is_iceberg_table(t)
                ]

                if not iceberg_tables:
                    st.info(f"No Iceberg tables found in `{sel_db}`.")
                else:
                    # Check which are registered
                    fqns_str = "', '".join(iceberg_tables[:500])
                    reg_sql  = f"""
                        SELECT table_fqn
                        FROM {STREAM_REGISTRY_TABLE}
                        WHERE table_fqn IN ('{fqns_str}')
                    """
                    reg_df       = cached_read_sql(reg_sql)
                    registered   = set(reg_df["table_fqn"].tolist()) if not reg_df.empty else set()
                    unregistered = [t for t in iceberg_tables if t not in registered]

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total Iceberg Tables", len(iceberg_tables))
                    col2.metric("Registered",           len(registered))
                    col3.metric("Unregistered",         len(unregistered),
                                delta=f"-{len(unregistered)} not in Zamboni",
                                delta_color="inverse")

                    if unregistered:
                        st.warning(f"{len(unregistered)} tables not in Zamboni")
                        unrg_df = pd.DataFrame({"table_fqn": unregistered})
                        st.dataframe(unrg_df, use_container_width=True, hide_index=True, height=300)

                        st.download_button(
                            "⬇️ Export unregistered list",
                            unrg_df.to_csv(index=False),
                            "unregistered_tables.csv",
                            "text/csv",
                        )

                        # ── Bulk Register Flow ────────────────────────────────
                        st.divider()
                        st.markdown("#### 🚀 Bulk Register Unregistered Tables")
                        st.caption(
                            "Select tables to register with a default policy template. "
                            "All selected tables will be registered in dry-run mode unless the sidebar toggle is off."
                        )
                        from app.components.auth import current_user as _bcu
                        from config.settings import APP_ENV as _benv
                        from config.settings import VALID_LAYERS, VALID_TIERS
                        from engine.core.config import apply_template, infer_template

                        sel_for_bulk = st.multiselect(
                            "Select tables to bulk-register",
                            unregistered,
                            key="sr_bulk_sel",
                        )
                        if sel_for_bulk:
                            bc1, bc2, bc3 = st.columns(3)
                            with bc1:
                                b_domain = st.text_input("Domain *", key="sr_bulk_domain")
                            with bc2:
                                b_layer = st.selectbox("Layer", VALID_LAYERS, key="sr_bulk_layer")
                            with bc3:
                                b_tier = st.selectbox("Tier", VALID_TIERS, index=1, key="sr_bulk_tier")

                            b_template = infer_template(b_layer, b_tier)
                            st.caption(f"Auto-inferred template: **{b_template}**")

                            if st.button("📥 Register Selected Tables", type="primary", key="sr_bulk_go"):
                                if not b_domain:
                                    st.error("Domain is required.")
                                else:
                                    from engine.core import registry
                                    ok_count, fail_count = 0, 0
                                    for fqn in sel_for_bulk:
                                        try:
                                            registry.register_table(
                                                table_fqn=fqn,
                                                domain=b_domain,
                                                layer=b_layer, tier=b_tier,
                                                environment="prod",
                                                table_format="iceberg",
                                                registered_by=f"streamlit:{_bcu()}",
                                                dry_run=is_dry_run(),
                                            )
                                            apply_template(fqn, b_template, dry_run=is_dry_run())
                                            ok_count += 1
                                        except Exception as be:
                                            fail_count += 1
                                            st.error(f"❌ {fqn}: {be}")

                                    audit(AuditEvent(
                                        actor=_bcu(),
                                        action_type=AuditAction.TABLE_REGISTER,
                                        page_source="10_Stale_Resources",
                                        target_type="table",
                                        target_id=f"{b_domain}/{sel_db}",
                                        domain=b_domain, environment=_benv,
                                        dry_run=is_dry_run(),
                                        status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                        after_value=f"count={ok_count},template={b_template}",
                                    ))
                                    if ok_count:
                                        st.success(f"✅ Registered {ok_count} table(s)"
                                                   + (" (dry run)" if is_dry_run() else ""))
                    else:
                        st.success(f"✅ All {len(iceberg_tables)} Iceberg tables in `{sel_db}` are registered.")

            except Exception as e:
                st.error(f"Scan failed: {e}")
    elif not sel_db:
        st.caption("Select a database above, then click Scan.")


# ── Tab 3: Orphaned S3 Locations ──────────────────────────────────────────────
with tab3:
    st.markdown("S3 prefixes that contain data but have **no corresponding Glue table**.")
    st.info("These are typically leftover from dropped tables where the S3 data was not cleaned up.")

    _s3_col1, _s3_col2 = st.columns([4, 1])
    with _s3_col1:
        s3_prefix = st.text_input(
            "S3 Base Prefix to Scan",
            placeholder="s3://your-staging-bucket/staging/finance/",
            key="sr_s3_prefix",
            help="Top-level S3 prefix to scan for orphaned data. "
                 "Large buckets may take several minutes.",
        )
    with _s3_col2:
        st.write("")  # spacer
        _s3_clicked = st.button(
            "🔍 Scan S3",
            key="sr_s3_scan",
            disabled=not bool(s3_prefix),
            type="primary",
        )
    st.caption(
        "ℹ️ Lists S3 prefixes not in the Glue catalog. "
        "Large buckets may take a few minutes."
    )

    if s3_prefix and _s3_clicked:
        with st.spinner(f"Scanning `{s3_prefix}`..."):
            try:
                from engine.utils.glue_client import get_tables
                from engine.utils.s3_client import parse_s3_uri

                bucket, prefix = parse_s3_uri(s3_prefix)

                import boto3

                from config.settings import AWS_REGION
                s3  = boto3.client("s3", region_name=AWS_REGION)
                res = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/")

                subprefixes = [
                    cp.get("Prefix", "")
                    for cp in res.get("CommonPrefixes", [])
                ]

                if not subprefixes:
                    st.info("No sub-prefixes found. Try a higher-level prefix.")
                else:
                    st.metric("S3 Sub-prefixes Found", len(subprefixes))
                    df = pd.DataFrame({
                        "s3_prefix": subprefixes,
                        "status":    ["⚠️ Check manually — cross-reference with Glue required"] * len(subprefixes),
                    })
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.caption("Full orphan detection requires CloudTrail integration (Phase 2 feature).")
            except Exception as e:
                st.error(f"S3 scan failed: {e}")


# ── Tab 4: Zero-Row Tables ────────────────────────────────────────────────────
with tab4:
    st.markdown("Tables registered in Zamboni that appear to have very few or no recent records.")
    st.info("These are candidates for review — they may be safe to deregister or archive entirely.")

    sel_domain_zr = domain_filter(key="sr_zr_domain")
    threshold     = st.number_input("Max row count threshold", value=0, min_value=0, key="sr_zr_threshold",
                                    help="Tables where the last archival exported fewer than this many rows")

    zero_sql = f"""
        SELECT
            table_fqn, domain, layer,
            MAX(rows_archived)      AS last_rows_archived,
            MAX(partition_date)     AS last_partition_archived
        FROM {EXECUTION_LOG_TABLE}
        WHERE engine = 'archival'
          AND status = 'SUCCESS'
          {("AND domain = '" + str(sel_domain_zr) + "'") if sel_domain_zr and isinstance(sel_domain_zr, str) else ""}
        GROUP BY table_fqn, domain, layer
        HAVING MAX(rows_archived) <= {threshold}
        ORDER BY last_rows_archived ASC
        LIMIT 100
    """

    if st.button("🔍 Find Low-Row Tables", key="sr_zero_run"):
        with st.spinner("Scanning execution log..."):
            try:
                df = cached_read_sql(zero_sql)
                if df.empty:
                    st.success("No low-row tables found with the selected threshold.")
                else:
                    st.warning(f"{len(df)} tables archived with ≤ {threshold} rows")
                    st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Query failed: {e}")
