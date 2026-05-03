"""
Zamboni — Non-Prod Lifecycle Manager
View tables by lifecycle state, submit exemptions, view deletion history.
"""
from datetime import UTC

import streamlit as st

from app.components.athena_runner import cached_read_sql, execute_write
from app.components.auth import check_login
from app.components.header import render as render_header
from app.components.sidebar import is_dry_run
from app.components.sidebar import render as render_sidebar
from app.components.status_badge import lifecycle as lifecycle_badge
from config.settings import NONPROD_REGISTRY_TABLE
from engine.core.audit import AuditAction, AuditEvent, audit

st.set_page_config(page_title="Zamboni — Non-Prod Lifecycle", page_icon="🗑️", layout="wide")
check_login()
render_sidebar()
render_header()

st.title("🗑️ Non-Prod Lifecycle Manager")
st.caption("Manage lifecycle states for preprod/dev/test tables. Submit exemptions to prevent deletion.")

# ── Environment selector ───────────────────────────────────────────────────────
env = st.selectbox("Environment", ["preprod", "dev", "test"], key="np_env")

tab1, tab2, tab3, tab4 = st.tabs(["📊 State Overview", "🛡️ Submit Exemption", "🙋 Claim Table", "⚫ Deletion History"])

# ── Tab 1: State Overview ──────────────────────────────────────────────────────
with tab1:
    state_filter = st.selectbox(
        "Lifecycle State",
        ["All", "ACTIVE", "STALE_CANDIDATE", "GREENZONE", "PENDING_DROP"],
        key="np_state",
    )

    state_clause = f"AND lifecycle_state = '{state_filter}'" if state_filter != "All" else ""
    sql = f"""
        SELECT
            table_fqn, domain, table_format,
            lifecycle_state, days_since_activity,
            last_query_at, last_write_at, created_at,
            greenzone_expires_at, pending_drop_expires_at,
            owner_exempted, is_backup_pattern, pattern_matched,
            first_seen_at
        FROM {NONPROD_REGISTRY_TABLE}
        WHERE environment = '{env}'
          AND lifecycle_state != 'DROPPED'
          {state_clause}
        ORDER BY lifecycle_state, days_since_activity DESC
        LIMIT 300
    """
    with st.spinner("Loading lifecycle data..."):
        try:
            df = cached_read_sql(sql)

            if df.empty:
                st.info(f"No tables found in `{env}` with the selected state.")
            else:
                # State distribution
                state_counts = df["lifecycle_state"].value_counts()
                cols = st.columns(len(state_counts))
                state_colors = {
                    "ACTIVE":          "🟢",
                    "STALE_CANDIDATE": "🟡",
                    "GREENZONE":       "🟠",
                    "PENDING_DROP":    "🔴",
                }
                for col, (state, count) in zip(cols, state_counts.items()):
                    icon = state_colors.get(state, "⚪")
                    col.metric(f"{icon} {state}", count)

                st.divider()

                # Highlight GREENZONE and PENDING_DROP
                df["lifecycle_state"] = df["lifecycle_state"].apply(lifecycle_badge)
                df["owner_exempted"]  = df["owner_exempted"].apply(
                    lambda x: "✅ Exempt" if x else ""
                )
                df["is_backup_pattern"] = df["is_backup_pattern"].apply(
                    lambda x: "🗂️ Backup" if x else ""
                )

                st.dataframe(df, use_container_width=True, hide_index=True, height=450)
                st.caption(f"{len(df)} tables shown")

        except Exception as e:
            st.error(f"Query failed: {e}")

# ── Tab 2: Submit Exemption ────────────────────────────────────────────────────
with tab2:
    st.markdown("#### Submit an Exemption")
    st.info("Submitting an exemption moves the table back to ACTIVE and prevents deletion for one more cycle. Provide a clear business reason.")

    exempt_fqn = st.text_input(
        "Table FQN",
        placeholder="glue_catalog.finance_preprod.finance_staging",
        key="np_exempt_fqn",
    )

    if exempt_fqn:
        check_sql = f"""
            SELECT table_fqn, lifecycle_state, domain, days_since_activity,
                   greenzone_expires_at, pending_drop_expires_at
            FROM {NONPROD_REGISTRY_TABLE}
            WHERE table_fqn = '{exempt_fqn}' LIMIT 1
        """
        try:
            check_df = cached_read_sql(check_sql)
            if check_df.empty:
                st.warning("Table not found in non-prod registry.")
            else:
                row = check_df.iloc[0]
                state = row.get("lifecycle_state", "")
                st.markdown(f"""
                **Current State:** {lifecycle_badge(state)}
                **Days Inactive:** {row.get('days_since_activity', '—')}
                **GREENZONE Expires:** {row.get('greenzone_expires_at', '—')}
                **Pending Drop At:** {row.get('pending_drop_expires_at', '—')}
                """)

                if state not in ("GREENZONE", "PENDING_DROP", "STALE_CANDIDATE"):
                    st.info(f"This table is in `{state}` — no exemption needed.")
                else:
                    reason = st.text_area(
                        "Business Reason (required)",
                        placeholder="This table is used by the Q2 reporting sprint, will be cleaned up by 2026-06-30",
                        key="np_exempt_reason",
                    )
                    dry_note = "⚠️ Dry Run ON — no changes will be written." if is_dry_run() else ""
                    if dry_note:
                        st.warning(dry_note)

                    if st.button("🛡️ Submit Exemption", type="primary"):
                        if not reason:
                            st.error("Please provide a business reason.")
                        else:
                            from datetime import datetime
                            now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                            update_sql = f"""
                                UPDATE {NONPROD_REGISTRY_TABLE}
                                SET lifecycle_state      = 'ACTIVE',
                                    previous_state       = '{state}',
                                    owner_exempted       = true,
                                    owner_response_at    = TIMESTAMP '{now}',
                                    owner_response_note  = '{reason.replace("'","''")}',
                                    state_changed_at     = TIMESTAMP '{now}'
                                WHERE table_fqn = '{exempt_fqn}'
                            """
                            execute_write(update_sql, workgroup="app", dry_run=is_dry_run())
                            from app.components.auth import current_user as _cu
                            audit(AuditEvent(
                                actor=_cu(),
                                action_type=AuditAction.LIFECYCLE_EXEMPTION,
                                page_source="9_NonProd_Lifecycle",
                                target_type="table",
                                target_id=exempt_fqn,
                                environment=env,
                                dry_run=is_dry_run(),
                                status="DRY_RUN" if is_dry_run() else "SUCCESS",
                                reason=reason,
                            ))
                            st.success(
                                f"✅ Exemption submitted for `{exempt_fqn}`"
                                + (" (dry run)" if is_dry_run() else "")
                            )
        except Exception as e:
            st.error(f"Error: {e}")

# ── Tab 3: Claim Table ────────────────────────────────────────────────────────
with tab3:
    st.markdown("#### 🙋 Claim This Table")
    st.caption(
        "Assign yourself as owner and reset the table to ACTIVE. "
        "Requires a reason. Audited."
    )
    from app.components.auth import current_user as _cu_claim
    claim_fqn = st.text_input("Table FQN to Claim", placeholder="glue_catalog.preprod_db.my_table", key="claim_fqn")
    claim_reason = st.text_area("Reason for claiming *", key="claim_reason",
                                 placeholder="Why are you claiming ownership of this table?")
    if st.button("🙋 Claim This Table", type="primary", key="claim_btn"):
        if not claim_fqn or not claim_reason.strip():
            st.error("Both table FQN and reason are required.")
        elif len(claim_reason.strip()) < 10:
            st.error("Reason must be at least 10 characters.")
        else:
            try:
                from datetime import UTC, datetime
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
                claim_sql = f"""
                    UPDATE {NONPROD_REGISTRY_TABLE}
                    SET lifecycle_state   = 'ACTIVE',
                        owner_email       = '{_cu_claim()}',
                        state_changed_at  = TIMESTAMP '{now}'
                    WHERE table_fqn = '{claim_fqn}'
                """
                from app.components.athena_runner import execute_write
                execute_write(claim_sql, workgroup="app", dry_run=is_dry_run())
                audit(AuditEvent(
                    actor=_cu_claim(),
                    action_type=AuditAction.CLAIM_TABLE,
                    page_source="9_NonProd_Lifecycle",
                    target_type="table", target_id=claim_fqn,
                    environment=env, dry_run=is_dry_run(),
                    status="DRY_RUN" if is_dry_run() else "SUCCESS",
                    reason=claim_reason.strip(),
                    after_value=f"owner={_cu_claim()},state=ACTIVE",
                ))
                st.success(f"✅ Table `{claim_fqn}` claimed and reset to ACTIVE"
                           + (" (dry run)" if is_dry_run() else ""))
            except Exception as ce:
                st.error(f"Claim failed: {ce}")

# ── Tab 4: Deletion History ────────────────────────────────────────────────────
with tab4:
    st.markdown("#### Recently Deleted Tables")
    hist_sql = f"""
        SELECT
            table_fqn, domain, dropped_at,
            bytes_reclaimed, s3_cleaned, catalog_dropped,
            previous_state
        FROM {NONPROD_REGISTRY_TABLE}
        WHERE environment    = '{env}'
          AND lifecycle_state = 'DROPPED'
          AND dropped_at     >= CURRENT_DATE - INTERVAL '90' DAY
        ORDER BY dropped_at DESC
        LIMIT 100
    """
    try:
        hist_df = cached_read_sql(hist_sql)
        if hist_df.empty:
            st.info("No tables deleted in the last 90 days.")
        else:
            total_reclaimed = hist_df["bytes_reclaimed"].sum()
            col1, col2 = st.columns(2)
            col1.metric("Tables Deleted (90d)", len(hist_df))
            col2.metric("Storage Reclaimed", f"{total_reclaimed/1e9:.1f} GB")
            st.divider()
            hist_df["s3_cleaned"]     = hist_df["s3_cleaned"].apply(lambda x: "✅" if x else "❌")
            hist_df["catalog_dropped"]= hist_df["catalog_dropped"].apply(lambda x: "✅" if x else "❌")
            hist_df["bytes_reclaimed"]= hist_df["bytes_reclaimed"].apply(
                lambda x: f"{x/1e9:.2f} GB" if x else "0 GB"
            )
            st.dataframe(hist_df, use_container_width=True, hide_index=True, height=350)
            st.download_button(
                "⬇️ Export",
                data=hist_df.to_csv(index=False),
                file_name="zamboni_deletion_history.csv",
                mime="text/csv",
            )
    except Exception as e:
        st.error(f"Query failed: {e}")
