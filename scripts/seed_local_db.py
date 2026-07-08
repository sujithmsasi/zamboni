"""
Zamboni -- Local Database Seed Script
Creates zamboni_local.db with realistic test data for all 12 UI pages.

Usage:
    python scripts/seed_local_db.py           # create/reset + seed
    python scripts/seed_local_db.py --reset   # wipe and re-seed

What gets created:
    domain_registry    5 domains
    stream_registry    25 tables across domains / layers / tiers
    hk_config          25 config rows (one per table)
    execution_log      300 execution rows (30 days of history)
    nonprod_registry   12 preprod/dev tables in various states
    home_snapshot      1 pre-aggregated summary row
    audit_log          40 audit events
"""
from __future__ import annotations

import json
import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ── Bootstrap path ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("ZAMBONI_LOCAL_MODE", "true")
os.environ.setdefault("ZAMBONI_TEST_MODE",  "true")

from engine.utils.local_db import create_tables, insert_rows, reset_db  # noqa: E402

# ── SQLite DDL (simplified from Iceberg DDL) ──────────────────────────────────

TABLES = {

"domain_registry": """
CREATE TABLE IF NOT EXISTS domain_registry (
    domain_name             TEXT PRIMARY KEY,
    environment             TEXT,
    owner_email             TEXT,
    hot_retention_days      INTEGER DEFAULT 30,
    stale_threshold_days    INTEGER DEFAULT 60,
    is_active               INTEGER DEFAULT 1,
    archive_enabled         INTEGER DEFAULT 0,
    digest_enabled          INTEGER DEFAULT 0,
    digest_email            TEXT,
    ci_number               TEXT,
    notes                   TEXT,
    created_at              TEXT,
    updated_at              TEXT,
    registered_at           TEXT DEFAULT (datetime('now')),
    display_name            TEXT,
    owner_name              TEXT,
    team_name               TEXT,
    description             TEXT,
    archive_duration_days   INTEGER DEFAULT 365,
    auto_delete_after_days  INTEGER DEFAULT 120,
    registered_by           TEXT DEFAULT ''
)""",

"stream_registry": """
CREATE TABLE IF NOT EXISTS stream_registry (
    table_fqn               TEXT PRIMARY KEY,
    domain                  TEXT,
    layer                   TEXT,
    tier                    TEXT,
    table_format            TEXT DEFAULT 'iceberg',
    environment             TEXT DEFAULT 'prod',
    owner_email             TEXT,
    ci_number               TEXT,
    hk_enabled              INTEGER DEFAULT 0,
    dry_run_until           TEXT,
    force_run               INTEGER DEFAULT 0,
    dependent_job_name      TEXT,
    dependent_job_type      TEXT DEFAULT 'glue',
    controlm_pipeline_job   TEXT,
    controlm_hk_job         TEXT,
    dependent_on_controlm_job TEXT,
    archive_enabled         INTEGER DEFAULT 0,
    archive_retention_days  INTEGER,
    archive_bucket          TEXT,
    lifecycle_enabled       INTEGER DEFAULT 0,
    processing_cadence      TEXT,
    properties_synced       INTEGER DEFAULT 0,
    last_execution_id       TEXT,
    registered_by           TEXT,
    registered_at           TEXT,
    updated_at              TEXT,
    database_name           TEXT,
    owner_name              TEXT DEFAULT '',
    notes                   TEXT DEFAULT ''
)""",

"controlm_jobs": """
CREATE TABLE IF NOT EXISTS controlm_jobs (
    job_name              TEXT PRIMARY KEY,
    job_type              TEXT DEFAULT 'controlm',
    description           TEXT DEFAULT '',
    domain                TEXT DEFAULT '',
    environment           TEXT DEFAULT 'prod',
    expected_start_time   TEXT DEFAULT '',
    expected_duration_min INTEGER DEFAULT 0,
    job_frequency         TEXT DEFAULT '',
    active                INTEGER DEFAULT 1,
    registered_by         TEXT DEFAULT 'system',
    created_at            TEXT,
    updated_at            TEXT
)""",
"hk_config": """
CREATE TABLE IF NOT EXISTS hk_config (
    table_fqn                       TEXT PRIMARY KEY,
    policy_template                 TEXT,
    compaction_strategy             TEXT DEFAULT 'binpack',
    compaction_target_file_size_mb  INTEGER DEFAULT 128,
    compaction_engine               TEXT DEFAULT 'athena',
    sort_order_cols                 TEXT,
    snapshot_retention_days         INTEGER DEFAULT 7,
    snapshot_min_to_keep            INTEGER DEFAULT 30,
    orphan_file_retention_days      INTEGER DEFAULT 3,
    orphan_cleanup_cadence_days     INTEGER DEFAULT 7,
    run_frequency                   TEXT DEFAULT 'daily',
    partition_column                TEXT,
    partition_filter_days           INTEGER,
    window_config                   TEXT,
    manually_overridden             INTEGER DEFAULT 0,
    override_notes                  TEXT,
    created_at                      TEXT,
    updated_at                      TEXT,
    partition_type                  TEXT DEFAULT 'date'
)""",

"execution_log": """
CREATE TABLE IF NOT EXISTS execution_log (
    execution_id            TEXT,
    run_id                  TEXT,
    engine                  TEXT,
    operation               TEXT,
    table_fqn               TEXT,
    domain                  TEXT,
    layer                   TEXT,
    tier                    TEXT,
    environment             TEXT DEFAULT 'prod',
    status                  TEXT,
    dry_run                 INTEGER DEFAULT 0,
    skip_reason             TEXT,
    error_message           TEXT,
    started_at              TEXT,
    completed_at            TEXT,
    duration_seconds        REAL,
    snapshots_before        INTEGER,
    snapshots_after         INTEGER,
    snapshots_expired       INTEGER,
    files_compacted         INTEGER,
    bytes_rewritten         INTEGER,
    orphan_files_deleted    INTEGER,
    bytes_archived          INTEGER,
    athena_query_id         TEXT,
    bytes_scanned           INTEGER DEFAULT 0,
    execution_date          TEXT
)""",

"nonprod_registry": """
CREATE TABLE IF NOT EXISTS nonprod_registry (
    table_fqn               TEXT PRIMARY KEY,
    domain                  TEXT,
    environment             TEXT,
    table_format            TEXT DEFAULT 'iceberg',
    lifecycle_state         TEXT DEFAULT 'ACTIVE',
    last_query_at           TEXT,
    last_write_at           TEXT,
    days_since_activity     INTEGER DEFAULT 0,
    stale_threshold_days    INTEGER DEFAULT 60,
    is_backup               INTEGER DEFAULT 0,
    is_backup_pattern       INTEGER DEFAULT 0,
    pattern_matched         TEXT DEFAULT '',
    owner_email             TEXT,
    owner_exempted          INTEGER DEFAULT 0,
    exemption_reason        TEXT,
    state_changed_at        TEXT,
    greenzone_expires_at    TEXT,
    pending_drop_expires_at TEXT,
    first_seen_at           TEXT,
    scan_count              INTEGER DEFAULT 0,
    created_at              TEXT,
    database_name           TEXT DEFAULT ''
)""",

"home_snapshot": """
CREATE TABLE IF NOT EXISTS home_snapshot (
    snapshot_date           TEXT PRIMARY KEY,
    generated_at            TEXT,
    generated_by            TEXT DEFAULT 'system',
    total_tables            INTEGER DEFAULT 0,
    hk_enabled_count        INTEGER DEFAULT 0,
    failures_7d             INTEGER DEFAULT 0,
    bytes_reclaimed_30d     INTEGER DEFAULT 0,
    fleet_coverage_json     TEXT DEFAULT '[]',
    compaction_needed_json  TEXT DEFAULT '[]',
    recent_failures_json    TEXT DEFAULT '[]',
    domain_stats_json       TEXT DEFAULT '[]',
    cost_summary_json       TEXT DEFAULT '[]'
)""",

"maintenance_locks": """
CREATE TABLE IF NOT EXISTS maintenance_locks (
    table_fqn               TEXT PRIMARY KEY,
    lock_owner              TEXT,
    operation               TEXT,
    acquired_at             TEXT,
    heartbeat_at            TEXT,
    expires_at              INTEGER
)""",

"vacuum_audit": """
CREATE TABLE IF NOT EXISTS vacuum_audit (
    run_id                  TEXT,
    table_fqn               TEXT,
    operation               TEXT,
    snapshots_before        INTEGER,
    snapshots_after         INTEGER,
    files_estimated         INTEGER,
    files_deleted           INTEGER,
    bytes_reclaimed         INTEGER,
    older_than_hours_used   INTEGER,
    sanity_pct              REAL,
    aborted                 INTEGER DEFAULT 0,
    aborted_reason          TEXT,
    lock_id                 TEXT,
    dry_run                 INTEGER DEFAULT 0,
    started_at              TEXT,
    completed_at             TEXT
)""",

"audit_log": """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id                TEXT PRIMARY KEY,
    timestamp               TEXT,
    actor                   TEXT,
    action_type             TEXT,
    page_source             TEXT,
    target_type             TEXT,
    target_id               TEXT,
    domain                  TEXT,
    environment             TEXT DEFAULT 'prod',
    dry_run                 INTEGER DEFAULT 1,
    status                  TEXT,
    reason                  TEXT,
    ticket_number           TEXT,
    before_value            TEXT,
    after_value             TEXT,
    error_message           TEXT,
    audit_date              TEXT
)""",

}


# ── Seed Data ─────────────────────────────────────────────────────────────────

def _now(offset_days: int = 0, offset_hours: int = 0) -> str:
    dt = datetime.now(UTC) - timedelta(
        days=offset_days, hours=offset_hours
    )
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _date(offset_days: int = 0) -> str:
    dt = datetime.now(UTC) - timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%d")


def seed_domains() -> list[dict]:
    return [
        {
            "domain_name":          "finance",
            "environment":          "prod",
            "owner_email":          "da-finance@company.com",
            "hot_retention_days":   30,
            "stale_threshold_days": 60,
            "is_active":            1,
            "archive_enabled":      1,
            "digest_enabled":       1,
            "digest_email":         "da-finance-dl@company.com",
            "ci_number":            "CI-10234",
            "notes":                "Finance domain - payment, claims, reconciliation",
            "created_at":           _now(120),
            "updated_at":           _now(5),
        },
        {
            "domain_name":          "ers",
            "environment":          "prod",
            "owner_email":          "da-ers@company.com",
            "hot_retention_days":   7,
            "stale_threshold_days": 30,
            "is_active":            1,
            "archive_enabled":      1,
            "digest_enabled":       0,
            "digest_email":         "",
            "ci_number":            "CI-10235",
            "notes":                "ERS domain - bookings, inventory",
            "created_at":           _now(110),
            "updated_at":           _now(10),
            "registered_at":        _now(110),
            "display_name":         "ERS",
            "owner_name":           "D&A ERS Lead",
            "team_name":            "Data & Analytics - ERS",
            "description":          "ERS domain covering bookings and inventory pipelines",
            "archive_duration_days": 365,
            "auto_delete_after_days": 120,
        },
        {
            "domain_name":          "membership",
            "environment":          "prod",
            "owner_email":          "da-membership@company.com",
            "hot_retention_days":   14,
            "stale_threshold_days": 45,
            "is_active":            1,
            "archive_enabled":      0,
            "digest_enabled":       1,
            "digest_email":         "",
            "ci_number":            "CI-10236",
            "notes":                "Membership domain - profiles, activity",
            "created_at":           _now(90),
            "updated_at":           _now(3),
        },
        {
            "domain_name":          "claims",
            "environment":          "prod",
            "owner_email":          "da-claims@company.com",
            "hot_retention_days":   90,
            "stale_threshold_days": 120,
            "is_active":            1,
            "archive_enabled":      1,
            "digest_enabled":       0,
            "digest_email":         "",
            "ci_number":            "CI-10237",
            "notes":                "Claims domain - 90-day retention for reopening",
            "created_at":           _now(80),
            "updated_at":           _now(7),
        },
        {
            "domain_name":          "travel",
            "environment":          "prod",
            "owner_email":          "da-travel@company.com",
            "hot_retention_days":   7,
            "stale_threshold_days": 30,
            "is_active":            1,
            "archive_enabled":      0,
            "digest_enabled":       0,
            "digest_email":         "",
            "ci_number":            "CI-10238",
            "notes":                "Travel domain - itineraries, bookings",
            "created_at":           _now(60),
            "updated_at":           _now(2),
        },
    ]


def seed_stream_registry() -> list[dict]:
    tables = []

    # ── Finance pipeline: APS payment stream ─────────────────────────────────
    fin_aps = [
        ("glue_catalog.finance_staging_db.fin_aps_payment_stg",  "staging",  "critical", "finance_staging_db",  "ACE-DA-FIN-APS-INGEST-PRD",    "ACE-DA-FIN-APS-INGEST-PRD",     "ACE-DA-FIN-APS-HK-PRD",  "daily"),
        ("glue_catalog.finance_datalake_db.fin_aps_payment_dl",  "datalake", "critical", "finance_datalake_db", "ACE-DA-FIN-APS-TRANSFORM-PRD", "ACE-DA-FIN-APS-INGEST-PRD",     "ACE-DA-FIN-APS-HK-PRD",  "daily"),
        ("glue_catalog.finance_base_db.fin_aps_payment_scd2",    "base",     "critical", "finance_base_db",     "ACE-DA-FIN-APS-SCD2-PRD",      "ACE-DA-FIN-APS-TRANSFORM-PRD",  "ACE-DA-FIN-APS-HK-PRD",  "daily"),
        ("glue_catalog.finance_master_db.fin_payment_master",    "master",   "standard", "finance_master_db",   "ACE-DA-FIN-ENT-MASTER-PRD",    "ACE-DA-FIN-APS-SCD2-PRD",       "ACE-DA-FIN-HK-PRD",      "weekly"),
    ]

    # ── Finance: claims stream ────────────────────────────────────────────────
    fin_claims = [
        ("glue_catalog.finance_staging_db.fin_claims_stg",       "staging",  "standard", "finance_staging_db",  "ACE-DA-FIN-CLM-INGEST-PRD",    "ACE-DA-FIN-CLM-INGEST-PRD",    "ACE-DA-FIN-CLM-HK-PRD",  "daily"),
        ("glue_catalog.finance_datalake_db.fin_claims_dl",        "datalake", "standard", "finance_datalake_db", "ACE-DA-FIN-CLM-TRANSFORM-PRD", "ACE-DA-FIN-CLM-INGEST-PRD",    "ACE-DA-FIN-CLM-HK-PRD",  "daily"),
        ("glue_catalog.finance_base_db.fin_claims_base",          "base",     "standard", "finance_base_db",     "ACE-DA-FIN-CLM-BASE-PRD",      "ACE-DA-FIN-CLM-TRANSFORM-PRD", "ACE-DA-FIN-CLM-HK-PRD",  "weekly"),
    ]

    # ── ERS: booking stream ───────────────────────────────────────────────────
    ers_bkg = [
        ("glue_catalog.ers_staging_db.ers_booking_stg",           "staging",  "critical", "ers_staging_db",  "ACE-DA-ERS-BKG-INGEST-PRD",    "ACE-DA-ERS-BKG-INGEST-PRD",    "ACE-DA-ERS-BKG-HK-PRD",  "every_trigger"),
        ("glue_catalog.ers_datalake_db.ers_booking_dl",            "datalake", "critical", "ers_datalake_db", "ACE-DA-ERS-BKG-TRANSFORM-PRD", "ACE-DA-ERS-BKG-INGEST-PRD",    "ACE-DA-ERS-BKG-HK-PRD",  "daily"),
        ("glue_catalog.ers_datalake_db.ers_inventory_dl",          "datalake", "standard", "ers_datalake_db", "ACE-DA-ERS-INV-TRANSFORM-PRD", "ACE-DA-ERS-INV-INGEST-PRD",    "ACE-DA-ERS-HK-PRD",      "daily"),
    ]

    # ── Membership: profile stream ────────────────────────────────────────────
    mbr = [
        ("glue_catalog.membership_staging_db.mbr_profile_stg",    "staging",  "standard", "membership_staging_db", "ACE-DA-MBR-PRF-INGEST-PRD",    "ACE-DA-MBR-PRF-INGEST-PRD",    "ACE-DA-MBR-HK-PRD",  "daily"),
        ("glue_catalog.membership_staging_db.mbr_activity_stg",   "staging",  "low",      "membership_staging_db", "ACE-DA-MBR-ACT-INGEST-PRD",    "ACE-DA-MBR-ACT-INGEST-PRD",    "ACE-DA-MBR-HK-PRD",  "weekly"),
    ]

    # ── Claims: flat structure (no master) ────────────────────────────────────
    clm = [
        ("glue_catalog.claims_staging_db.clm_incident_stg",       "staging",  "standard", "claims_staging_db", "ACE-DA-CLM-INC-INGEST-PRD",    "ACE-DA-CLM-INC-INGEST-PRD",    "ACE-DA-CLM-HK-PRD",  "daily"),
        ("glue_catalog.claims_staging_db.clm_settlement_stg",     "staging",  "standard", "claims_staging_db", "ACE-DA-CLM-STL-INGEST-PRD",    "ACE-DA-CLM-STL-INGEST-PRD",    "ACE-DA-CLM-HK-PRD",  "daily"),
    ]

    # ── Tables with issues (circuit breaker, disabled, ramp-up) ──────────────
    special = [
        ("glue_catalog.finance_datalake_db.fin_reconcile_dl",     "datalake", "low",      "finance_datalake_db", "ACE-DA-FIN-REC-TRANSFORM-PRD", "ACE-DA-FIN-REC-INGEST-PRD",    "ACE-DA-FIN-HK-PRD",  "weekly"),
        ("glue_catalog.ers_staging_db.ers_pricing_stg",           "staging",  "low",      "ers_staging_db",      "ACE-DA-ERS-PRC-INGEST-PRD",    "ACE-DA-ERS-PRC-INGEST-PRD",    "ACE-DA-ERS-HK-PRD",  "daily"),
        ("glue_catalog.membership_staging_db.mbr_rewards_stg",    "staging",  "low",      "membership_staging_db", "ACE-DA-MBR-RWD-INGEST-PRD",  "ACE-DA-MBR-RWD-INGEST-PRD",    "ACE-DA-MBR-HK-PRD",  "monthly"),
    ]

    all_groups = fin_aps + fin_claims + ers_bkg + mbr + clm + special

    hk_enabled_set = {t[0] for t in (fin_aps + fin_claims + ers_bkg[:2] + mbr[:1] + clm)}
    rampup_set     = {ers_bkg[2][0], mbr[1][0]}

    # ── Dual-Optimizer conflict demo table (Workstream A / Phase 1c) ────────
    # governance.dual_optimizer_report() / fleet_conflict_summary() need at
    # least one hk_enabled table with an aws_opt_* flag set to render a
    # non-empty Governance tab -- none of the seeded rows had this set
    # before Phase 1c. Also carries the recovery-tool demo's simulated
    # "current" metadata_location (see get_rollback_candidates()).
    _CONFLICT_DEMO_FQN = "glue_catalog.finance_master_db.fin_payment_master"
    _CONFLICT_DEMO_LOCATION = (
        "s3://zamboni-metadata-demo/finance_master_db/fin_payment_master/"
        "metadata/00042-c9f1a2e0-cur.metadata.json"
    )
    for i, (fqn, layer, tier, db_name,
            pipeline_job, dep_job, hk_job, freq) in enumerate(all_groups):

        domain = fqn.split(".")[1].split("_")[0]

        hk_en    = 1 if fqn in hk_enabled_set else 0
        dry_until = None
        if fqn in rampup_set:
            # Found, not previously exercised: _date(-7 + 14) == _date(7) is
            # 7 days AGO, not "expires in 7 days" as the old comment claimed
            # -- the sign was backwards, so in_dry_run/dry_run_adoption were
            # silently always empty in local mode. _date(-7) is 7 days
            # ahead (_date(offset_days) = now - offset_days).
            dry_until = _date(-7)  # active ramp-up, expires in 7 days

        is_conflict_demo = fqn == _CONFLICT_DEMO_FQN

        tables.append({
            "table_fqn":               fqn,
            "domain":                  domain,
            "layer":                   layer,
            "tier":                    tier,
            "table_format":            "iceberg",
            "environment":             "prod",
            "owner_email":             f"da-{domain}@company.com",
            "ci_number":               f"CI-{10300 + i}",
            "hk_enabled":              hk_en,
            "dry_run_until":           dry_until,
            "force_run":               0,
            "dependent_job_name":      dep_job,
            "dependent_job_type":      "glue",
            "controlm_pipeline_job":   pipeline_job,
            "controlm_hk_job":         hk_job,
            "dependent_on_controlm_job": dep_job,
            "archive_enabled":         1 if layer == "staging" else 0,
            "archive_retention_days":  30 if layer == "staging" else None,
            "archive_bucket":          None,
            "lifecycle_enabled":       0,
            "processing_cadence":      "daily" if layer == "staging" else "weekly",
            "properties_synced":       1,
            "last_execution_id":       None,
            "registered_by":           "streamlit:admin",
            "registered_at":           _now(60 - i),
            "updated_at":              _now(random.randint(0, 10)),
            "database_name":           db_name,
            "aws_opt_compaction":      1 if is_conflict_demo else 0,
            "aws_opt_retention":       0,
            "aws_opt_orphan":          0,
            "aws_opt_checked_at":      _now(0, 2) if is_conflict_demo else None,
            "metadata_location":       _CONFLICT_DEMO_LOCATION if is_conflict_demo else None,
        })

    return tables


def seed_hk_config(stream_rows: list[dict]) -> list[dict]:
    configs = []
    template_map = {
        "staging":  ("STAGING_DEFAULT",  "binpack", "athena",  7,  3,  "daily"),
        "datalake": ("DATALAKE_DEFAULT", "binpack", "athena",  7,  5,  "daily"),
        "base":     ("BASE_SCD2",        "sort",    "glue",   14, 10,  "weekly"),
        "master":   ("MASTER_DEFAULT",   "zorder",  "glue",   30, 14,  "weekly"),
    }
    for row in stream_rows:
        layer = row["layer"]
        tmpl, strategy, engine, snap_days, orphan_days, freq = template_map.get(
            layer, ("STAGING_DEFAULT", "binpack", "athena", 7, 3, "daily")
        )
        run_freq = row.get("processing_cadence") or freq
        configs.append({
            "table_fqn":                       row["table_fqn"],
            "policy_template":                 tmpl,
            "compaction_strategy":             strategy,
            "compaction_target_file_size_mb":  128,
            "compaction_engine":               engine,
            "sort_order_cols":                 "partition_date" if strategy in ("sort","zorder") else None,
            "snapshot_retention_days":         snap_days,
            "snapshot_min_to_keep":            30,
            "orphan_file_retention_days":      orphan_days,
            "orphan_cleanup_cadence_days":     7,
            "run_frequency":                   run_freq,
            "partition_column":                "partition_date",
            "partition_filter_days":           90,
            "window_config":                   json.dumps({
                "type": "post_batch", "timezone": "America/Los_Angeles",
                "delay_minutes": 30, "duration_hours": 4,
                "blackout_hours": [6,7,8,9,18,19,20,21],
            }),
            "manually_overridden":             0,
            "override_notes":                  None,
            "created_at":                      _now(60),
            "updated_at":                      _now(random.randint(0,10)),
        })
    return configs


def seed_execution_log(stream_rows: list[dict]) -> list[dict]:
    rows = []
    statuses  = ["SUCCESS"] * 8 + ["SKIPPED"] * 3 + ["FAILURE"] * 1
    skip_reasons = [
        "SKIP_NOT_DUE", "SKIP_HEALTHY", "SKIP_OUTSIDE_WINDOW",
        "SKIP_UPSTREAM_PENDING",
    ]
    errors = [
        "Athena query timeout after 1800s",
        "INVALID_NON_DETERMINISTIC_EXPRESSIONS in MERGE",
        "S3 access denied on archive bucket",
    ]
    for day in range(30):
        run_id = f"hk-{_date(day)}-{uuid.uuid4().hex[:6]}"
        for tbl in random.sample(stream_rows, min(15, len(stream_rows))):
            fqn    = tbl["table_fqn"]
            domain = tbl["domain"]
            layer  = tbl["layer"]
            tier   = tbl["tier"]
            status = random.choice(statuses)

            for op in ["hk_run"] + (["compaction", "vacuum"] if status == "SUCCESS" else []):
                started   = _now(day, random.randint(0, 20))
                duration  = random.uniform(30, 600) if status == "SUCCESS" else random.uniform(5, 120)
                completed = (datetime.fromisoformat(started.replace(" ", "T") + "+00:00")
                             + timedelta(seconds=duration)).strftime("%Y-%m-%d %H:%M:%S")
                scanned   = random.randint(500_000_000, 50_000_000_000) if status == "SUCCESS" else 0

                rows.append({
                    "execution_id":     str(uuid.uuid4()),
                    "run_id":           run_id,
                    "engine":           "hk",
                    "operation":        op,
                    "table_fqn":        fqn,
                    "domain":           domain,
                    "layer":            layer,
                    "tier":             tier,
                    "environment":      "prod",
                    "status":           status if op == "hk_run" else
                                        ("SUCCESS" if status == "SUCCESS" else "SKIPPED"),
                    "dry_run":          0,
                    "skip_reason":      random.choice(skip_reasons) if status == "SKIPPED" else None,
                    "error_message":    random.choice(errors) if status == "FAILURE" else None,
                    "started_at":       started,
                    "completed_at":     completed,
                    "duration_seconds": round(duration, 1),
                    "snapshots_before": random.randint(30, 200),
                    "snapshots_after":  random.randint(10, 30) if op == "vacuum" else None,
                    "snapshots_expired":random.randint(10, 100) if op == "vacuum" else None,
                    "files_compacted":  random.randint(50, 2000) if op == "compaction" else None,
                    "bytes_rewritten":  random.randint(100_000_000, 5_000_000_000) if op == "compaction" else None,
                    "orphan_files_deleted": random.randint(0, 50) if op == "orphan_cleanup" else None,
                    "bytes_archived":   None,
                    "athena_query_id":  f"query-{uuid.uuid4().hex[:12]}",
                    "bytes_scanned":    scanned,
                    "execution_date":   _date(day),
                })

    return rows


def seed_rollback_demo_rows(stream_rows: list[dict]) -> list[dict]:
    """
    Realistic execution_log rows with metadata_location_before/after for
    engine.core.recovery.get_rollback_candidates()'s CLI demo (Workstream A
    / Phase 1c). seed_execution_log() seeds 30 days of history but never
    populates the Phase 1a/1b safety-core columns, so without this the
    local rollback-candidates list would be empty for every table.
    """
    fqn  = "glue_catalog.finance_master_db.fin_payment_master"
    row  = next(r for r in stream_rows if r["table_fqn"] == fqn)
    base = "s3://zamboni-metadata-demo/finance_master_db/fin_payment_master/metadata"

    # (operation, days_ago, metadata_location_before, metadata_location_after)
    steps = [
        ("vacuum",   3, f"{base}/00040-a1b2c3d4-old.metadata.json", f"{base}/00041-b2c3d4e5-mid.metadata.json"),
        ("optimize", 1, f"{base}/00041-b2c3d4e5-mid.metadata.json", f"{base}/00042-c9f1a2e0-cur.metadata.json"),
    ]
    rows = []
    for op, days_ago, before, after in steps:
        rows.append({
            "execution_id":             str(uuid.uuid4()),
            "run_id":                   f"hk-{_date(days_ago)}-{uuid.uuid4().hex[:6]}",
            "engine":                   "hk",
            "operation":                op,
            "table_fqn":                fqn,
            "domain":                   row["domain"],
            "layer":                    row["layer"],
            "tier":                     row["tier"],
            "environment":              "prod",
            "status":                   "SUCCESS",
            "dry_run":                  0,
            "skip_reason":              None,
            "error_message":            None,
            "started_at":               _now(days_ago, 2),
            "completed_at":             _now(days_ago, 1),
            "duration_seconds":         45.0,
            "snapshots_before":         120,
            "snapshots_after":          40 if op == "vacuum" else 121,
            "snapshots_expired":        80 if op == "vacuum" else None,
            "files_compacted":         850 if op == "optimize" else None,
            "bytes_rewritten":          2_400_000_000 if op == "optimize" else None,
            "orphan_files_deleted":     210 if op == "vacuum" else None,
            "bytes_archived":           None,
            "athena_query_id":          f"query-{uuid.uuid4().hex[:12]}",
            "bytes_scanned":            1_800_000_000,
            "execution_date":           _date(days_ago),
            "lock_id":                  f"{fqn}:demo-owner",
            "metadata_location_before": before,
            "metadata_location_after":  after,
            "snapshot_id_before":       9000 + days_ago,
            "snapshot_id_after":        9000 + days_ago - 1,
            "integrity_status":        "VERIFIED",
        })
    return rows


def seed_vacuum_audit_demo_rows(stream_rows: list[dict]) -> list[dict]:
    """
    vacuum_audit is created by TABLES's DDL but no seeder ever populated it
    (Phase 1b's "real local dry run" note only ever produced one row by hand
    during that phase's own manual verification) -- so the Health Dashboard's
    "Storage Reclaimed" chart and executions_svc.py's
    _reclaimed_storage_trend()/_top_tables_by_reclaim() have nothing to show
    without this. ~18 realistic runs across 30 days, most successful, two
    ORPHAN_SANITY_ABORT aborts for authenticity (contracts.md §5-A step b).
    """
    candidates = [r for r in stream_rows if r["hk_enabled"]]
    rows = []
    for day in [1, 2, 4, 5, 7, 8, 10, 12, 13, 15, 17, 19, 20, 22, 24, 26, 27, 29]:
        tbl = random.choice(candidates)
        aborted = day in (12, 24)  # a couple of sanity-abort demo rows
        snapshots_before = random.randint(60, 220)
        bytes_reclaimed = 0 if aborted else random.randint(500_000_000, 6_000_000_000)
        rows.append({
            "run_id":                f"hk-{_date(day)}-{uuid.uuid4().hex[:6]}",
            "table_fqn":              tbl["table_fqn"],
            "operation":              "vacuum",
            "snapshots_before":       snapshots_before,
            "snapshots_after":        snapshots_before if aborted else random.randint(10, 40),
            "files_estimated":        random.randint(100, 3000),
            "files_deleted":          0 if aborted else random.randint(50, 2000),
            "bytes_reclaimed":        bytes_reclaimed,
            "older_than_hours_used":  96,
            "sanity_pct":             round(random.uniform(22, 35), 1) if aborted else round(random.uniform(2, 18), 1),
            "aborted":                1 if aborted else 0,
            "aborted_reason":         "ORPHAN_SANITY_ABORT" if aborted else None,
            "lock_id":                f"{tbl['table_fqn']}:demo-owner",
            "dry_run":                0,
            "started_at":             _now(day, 1),
            "completed_at":           _now(day, 0),
        })
    return rows


def seed_archival_demo_rows(stream_rows: list[dict]) -> list[dict]:
    """
    execution_log has zero engine='archival' rows -- seed_execution_log()
    only ever generates 'hk' engine ops. Needed for the Reclaimed Storage
    chart's archival series and the cost report's gb_archived total.
    """
    candidates = [r for r in stream_rows if r["layer"] == "staging"] or stream_rows
    rows = []
    for day in [2, 6, 9, 14, 16, 21, 23, 28]:
        tbl = random.choice(candidates)
        started = _now(day, 3)
        rows.append({
            "execution_id":     str(uuid.uuid4()),
            "run_id":           f"archival-{_date(day)}-{uuid.uuid4().hex[:6]}",
            "engine":           "archival",
            "operation":        "export_then_delete",
            "table_fqn":        tbl["table_fqn"],
            "domain":           tbl["domain"],
            "layer":            tbl["layer"],
            "tier":             tbl["tier"],
            "environment":      "prod",
            "status":           "SUCCESS",
            "dry_run":          0,
            "skip_reason":      None,
            "error_message":    None,
            "started_at":       started,
            "completed_at":     _now(day, 2),
            "duration_seconds": round(random.uniform(120, 900), 1),
            "bytes_archived":   random.randint(1_000_000_000, 12_000_000_000),
            "rows_archived":    random.randint(10_000, 500_000),
            "athena_query_id":  f"query-{uuid.uuid4().hex[:12]}",
            "bytes_scanned":    random.randint(1_000_000_000, 8_000_000_000),
            "execution_date":   _date(day),
        })
    return rows


def seed_nonprod_registry() -> list[dict]:
    states = [
        ("ACTIVE",         0),
        ("ACTIVE",         5),
        ("STALE_CANDIDATE",32),
        ("STALE_CANDIDATE",45),
        ("GREENZONE",      62),
        ("GREENZONE",      70),
        ("PENDING_DROP",   91),
        ("DROPPED",        120),
        ("ACTIVE",         3),
        ("STALE_CANDIDATE",38),
        ("ACTIVE",         1),
        ("GREENZONE",      65),
    ]
    domains = ["finance","finance","ers","ers","membership","claims",
               "ers","finance","membership","travel","ers","claims"]
    names   = [
        "fin_payment_backup_apr26", "fin_reconcile_test_copy",
        "ers_booking_preprod", "ers_inventory_dev_sujith",
        "mbr_profile_uat", "clm_incident_preprod",
        "ers_pricing_dev_old", "fin_claims_temp_2026_01_15",
        "mbr_activity_test", "trl_booking_stale_bak",
        "ers_booking_staging_dev", "clm_settlement_greenzone_test",
    ]
    envs = ["preprod","preprod","preprod","dev","preprod","preprod",
            "dev","dev","test","dev","dev","preprod"]

    rows = []
    for i, ((state, inact_days), domain, name, env) in enumerate(
        zip(states, domains, names, envs)
    ):
        db  = f"{env}_{domain}_db"
        fqn = f"glue_catalog.{db}.{name}"
        is_backup = 1 if any(p in name for p in
                             ["backup","copy","bak","old","temp","stale"]) else 0
        rows.append({
            "table_fqn":          fqn,
            "domain":             domain,
            "environment":        env,
            "table_format":       "iceberg",
            "lifecycle_state":    state,
            "last_query_at":      _now(inact_days + random.randint(0,5)),
            "last_write_at":      _now(inact_days + random.randint(0,10)),
            "days_since_activity":inact_days,
            "is_backup":          is_backup,
            "owner_email":        f"da-{domain}@company.com",
            "state_changed_at":   _now(random.randint(0, inact_days)),
            "scan_count":         random.randint(1, 10),
            "exemption_reason":   None,
            "created_at":         _now(inact_days + 10),
        })
    return rows


def seed_home_snapshot(stream_rows: list[dict]) -> list[dict]:
    enabled = sum(1 for r in stream_rows if r["hk_enabled"])
    total   = len(stream_rows)
    return [{
        "snapshot_date":           _date(0),
        "environment":             "prod",
        "total_tables":            total,
        "hk_enabled_count":        enabled,
        "hk_coverage_pct":         round(enabled / total * 100, 1),
        "dry_run_count":           2,
        "tables_needing_hk":       random.randint(3, 8),
        "gb_compacted_today":      round(random.uniform(10, 80), 2),
        "snapshots_expired_today": random.randint(200, 800),
        "failures_today":          random.randint(0, 2),
        "circuit_breakers_open":   1,
        "generated_at":            _now(0),
    }]


def seed_audit_log() -> list[dict]:
    actors  = ["sujith", "admin", "da-team-user1", "da-team-user2"]
    actions = [
        ("hk_enable",              "table", "SUCCESS", 0),
        ("hk_disable",             "table", "SUCCESS", 0),
        ("policy_change",          "table", "SUCCESS", 0),
        ("dry_run_promote",        "table", "SUCCESS", 0),
        ("circuit_breaker_reenable","table","SUCCESS", 0),
        ("domain_create",          "domain","SUCCESS", 0),
        ("table_register",         "table", "SUCCESS", 0),
        ("lifecycle_exemption",    "table", "SUCCESS", 0),
        ("settings_change",        "setting","SUCCESS",0),
        ("hk_enable",              "table", "REJECTED",0),
        ("cancel_query",           "query", "SUCCESS", 0),
        ("bulk_template_apply",    "table", "DRY_RUN", 1),
    ]
    rows = []
    for i, (action, ttype, status, dry) in enumerate(actions * 3):
        ts = _now(i // 3, (i % 24))
        rows.append({
            "audit_id":      str(uuid.uuid4()),
            "timestamp":     ts,
            "actor":         random.choice(actors),
            "action_type":   action,
            "page_source":   f"{random.randint(1,10)}_Page",
            "target_type":   ttype,
            "target_id":     "glue_catalog.finance_staging_db.fin_aps_payment_stg",
            "domain":        "finance",
            "environment":   "prod",
            "dry_run":       dry,
            "status":        status,
            "reason":        "Monthly governance review" if status != "DRY_RUN" else None,
            "ticket_number": f"CHG{random.randint(10000,99999)}" if status == "SUCCESS" and not dry else None,
            "before_value":  None,
            "after_value":   None,
            "error_message": "Reason required for this action" if status == "REJECTED" else None,
            "audit_date":    ts[:10],
        })
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    reset = "--reset" in sys.argv or True  # always reset for clean seed

    if reset:
        print("Resetting local database...")
        reset_db()

    print("Creating tables...")
    create_tables(TABLES)

    # Schema migrations: add missing columns to existing tables
    # Safe to run repeatedly — ALTER TABLE is skipped if column exists
    _migrations = [
        ("execution_log",    "rows_archived",      "INTEGER DEFAULT 0"),
        ("execution_log",    "vacuum_iterations",  "INTEGER DEFAULT 1"),
        ("execution_log",    "oldest_snapshot_id", "TEXT"),
        ("execution_log",    "newest_snapshot_id", "TEXT"),
        ("nonprod_registry", "dropped_at",         "TEXT"),
        ("nonprod_registry", "bytes_reclaimed",    "INTEGER DEFAULT 0"),
        ("nonprod_registry", "s3_cleaned",         "INTEGER DEFAULT 0"),
        ("nonprod_registry", "catalog_dropped",    "INTEGER DEFAULT 0"),
        ("nonprod_registry", "previous_state",     "TEXT"),
        ("nonprod_registry", "is_backup_pattern",  "INTEGER DEFAULT 0"),
        ("stream_registry",  "owner_name",                       "TEXT DEFAULT ''"),
        ("stream_registry",  "notes",                             "TEXT DEFAULT ''"),
        ("stream_registry",  "partition_type",                   "TEXT DEFAULT 'date'"),
        ("stream_registry",  "controlm_job_start_time",          "TEXT DEFAULT '02:00'"),
        ("stream_registry",  "controlm_expected_duration_min",   "INTEGER DEFAULT 0"),
        ("hk_config",        "gate1_enabled",                    "INTEGER DEFAULT 0"),
        ("hk_config",        "gate2_enabled",                    "INTEGER DEFAULT 1"),
        ("hk_config",        "gate3_enabled",                    "INTEGER DEFAULT 1"),
        ("hk_config",        "partition_type",     "TEXT DEFAULT 'date'"),
        ("domain_registry",  "display_name",       "TEXT"),
        ("domain_registry",  "registered_at",      "TEXT DEFAULT (datetime('now'))"),
        ("domain_registry",  "owner_name",         "TEXT DEFAULT ''"),
        ("domain_registry",  "team_name",          "TEXT DEFAULT ''"),
        ("domain_registry",  "description",        "TEXT DEFAULT ''"),
        ("domain_registry",  "archive_duration_days",  "INTEGER DEFAULT 365"),
        ("domain_registry",  "auto_delete_after_days", "INTEGER DEFAULT 120"),
        ("domain_registry",  "registered_by",      "TEXT DEFAULT ''"),
        # ── Safety Core (Workstream A / Phase 1a — contracts.md §3.2) ───────
        ("stream_registry",  "aws_opt_compaction", "INTEGER DEFAULT 0"),
        ("stream_registry",  "aws_opt_retention",  "INTEGER DEFAULT 0"),
        ("stream_registry",  "aws_opt_orphan",     "INTEGER DEFAULT 0"),
        ("stream_registry",  "aws_opt_checked_at", "TEXT"),
        ("hk_config",        "gate0_override_until",  "TEXT"),
        ("hk_config",        "gate0_override_reason", "TEXT"),
        ("hk_config",        "gate0_override_by",     "TEXT"),
        ("execution_log",    "lock_id",                  "TEXT"),
        ("execution_log",    "metadata_location_before", "TEXT"),
        ("execution_log",    "metadata_location_after",  "TEXT"),
        ("execution_log",    "snapshot_id_before",       "INTEGER"),
        ("execution_log",    "snapshot_id_after",        "INTEGER"),
        ("execution_log",    "integrity_status",         "TEXT"),
        # ── Recovery tooling (Workstream A / Phase 1c) ───────────────────────
        # Local-mode-only simulation column: recovery.py's rollback path
        # reads/writes this to simulate Glue's Parameters['metadata_location']
        # since there is no live Glue catalog in ZAMBONI_LOCAL_MODE (same gap
        # integrity_checker.capture_state() documents). NOT part of
        # contracts.md's locked Athena DDL -- in real mode the current
        # pointer always comes from live Glue Parameters, never this table.
        ("stream_registry",  "metadata_location",        "TEXT"),
        ("controlm_jobs",    "job_frequency",             "TEXT DEFAULT ''"),
    ]
    from engine.utils.local_db import get_connection as _gc
    _conn = _gc()
    _migrated = 0
    for _tbl, _col, _typ in _migrations:
        try:
            _conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_typ}")
            _conn.commit()
            _migrated += 1
        except Exception:
            pass  # column already exists
    if _migrated:
        print(f"  {_migrated} schema migration(s) applied")

    print("Seeding domain_registry...")
    domains = seed_domains()
    n = insert_rows("domain_registry", domains)
    print(f"  {n} domains")

    print("Seeding stream_registry...")
    streams = seed_stream_registry()
    n = insert_rows("stream_registry", streams)
    print(f"  {n} tables")

    print("Seeding hk_config...")
    configs = seed_hk_config(streams)
    n = insert_rows("hk_config", configs)
    print(f"  {n} configs")

    print("Seeding execution_log...")
    executions = seed_execution_log(streams)
    n = insert_rows("execution_log", executions)
    print(f"  {n} execution records")

    # Separate insert_rows() call: it derives its INSERT column list from
    # rows[0].keys() alone, so a batch mixing seed_execution_log()'s narrower
    # row shape with these Safety-Core-column rows would silently drop the
    # extra columns on every row (including these).
    rollback_demo_rows = seed_rollback_demo_rows(streams)
    n = insert_rows("execution_log", rollback_demo_rows)
    print(f"  {n} rollback-candidate demo records")

    archival_demo_rows = seed_archival_demo_rows(streams)
    n = insert_rows("execution_log", archival_demo_rows)
    print(f"  {n} archival demo records")

    print("Seeding vacuum_audit...")
    vacuum_audit_rows = seed_vacuum_audit_demo_rows(streams)
    n = insert_rows("vacuum_audit", vacuum_audit_rows)
    print(f"  {n} vacuum_audit records")

    print("Seeding nonprod_registry...")
    nonprod = seed_nonprod_registry()
    n = insert_rows("nonprod_registry", nonprod)
    print(f"  {n} nonprod tables")

    print("Seeding home_snapshot...")
    snapshot = seed_home_snapshot(streams)
    n = insert_rows("home_snapshot", snapshot)
    print(f"  {n} snapshot row")

    print("Seeding audit_log...")
    audit = seed_audit_log()
    n = insert_rows("audit_log", audit)
    print(f"  {n} audit events")

    print()
    print("=" * 50)
    print("Local database ready: zamboni_local.db")
    print()
    print("To start Zamboni locally:")
    print("  set ZAMBONI_LOCAL_MODE=true    (Windows)")
    print("  set ZAMBONI_LOCAL_DB=zamboni_local.db")
    print("  streamlit run app/Home.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
