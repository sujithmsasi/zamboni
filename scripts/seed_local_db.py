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
    updated_at              TEXT
)""",

"stream_registry": """
CREATE TABLE IF NOT EXISTS stream_registry (
    table_fqn               TEXT PRIMARY KEY,
    stream_id               TEXT,
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
    database_name           TEXT
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
    updated_at                      TEXT
)""",

"execution_log": """
CREATE TABLE IF NOT EXISTS execution_log (
    execution_id            TEXT,
    run_id                  TEXT,
    engine                  TEXT,
    operation               TEXT,
    table_fqn               TEXT,
    stream_id               TEXT,
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
    days_since_activity     INTEGER,
    is_backup               INTEGER DEFAULT 0,
    owner_email             TEXT,
    state_changed_at        TEXT,
    scan_count              INTEGER DEFAULT 0,
    exemption_reason        TEXT,
    created_at              TEXT
)""",

"home_snapshot": """
CREATE TABLE IF NOT EXISTS home_snapshot (
    snapshot_date           TEXT PRIMARY KEY,
    environment             TEXT DEFAULT 'prod',
    total_tables            INTEGER DEFAULT 0,
    hk_enabled_count        INTEGER DEFAULT 0,
    hk_coverage_pct         REAL DEFAULT 0,
    dry_run_count           INTEGER DEFAULT 0,
    tables_needing_hk       INTEGER DEFAULT 0,
    gb_compacted_today      REAL DEFAULT 0,
    snapshots_expired_today INTEGER DEFAULT 0,
    failures_today          INTEGER DEFAULT 0,
    circuit_breakers_open   INTEGER DEFAULT 0,
    generated_at            TEXT
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
        ("glue_catalog.finance_staging_db.fin_aps_payment_stg",  "staging",  "critical", "STR-FIN-APS-0001", "finance_staging_db",  "ACE-DA-FIN-APS-INGEST-PRD",    "ACE-DA-FIN-APS-INGEST-PRD",     "ACE-DA-FIN-APS-HK-PRD",  "daily"),
        ("glue_catalog.finance_datalake_db.fin_aps_payment_dl",  "datalake", "critical", "STR-FIN-APS-0001", "finance_datalake_db", "ACE-DA-FIN-APS-TRANSFORM-PRD", "ACE-DA-FIN-APS-INGEST-PRD",     "ACE-DA-FIN-APS-HK-PRD",  "daily"),
        ("glue_catalog.finance_base_db.fin_aps_payment_scd2",    "base",     "critical", "STR-FIN-APS-0001", "finance_base_db",     "ACE-DA-FIN-APS-SCD2-PRD",      "ACE-DA-FIN-APS-TRANSFORM-PRD",  "ACE-DA-FIN-APS-HK-PRD",  "daily"),
        ("glue_catalog.finance_master_db.fin_payment_master",    "master",   "standard", "STR-FIN-ENT-0001", "finance_master_db",   "ACE-DA-FIN-ENT-MASTER-PRD",    "ACE-DA-FIN-APS-SCD2-PRD",       "ACE-DA-FIN-HK-PRD",      "weekly"),
    ]

    # ── Finance: claims stream ────────────────────────────────────────────────
    fin_claims = [
        ("glue_catalog.finance_staging_db.fin_claims_stg",       "staging",  "standard", "STR-FIN-CLM-0001", "finance_staging_db",  "ACE-DA-FIN-CLM-INGEST-PRD",    "ACE-DA-FIN-CLM-INGEST-PRD",    "ACE-DA-FIN-CLM-HK-PRD",  "daily"),
        ("glue_catalog.finance_datalake_db.fin_claims_dl",        "datalake", "standard", "STR-FIN-CLM-0001", "finance_datalake_db", "ACE-DA-FIN-CLM-TRANSFORM-PRD", "ACE-DA-FIN-CLM-INGEST-PRD",    "ACE-DA-FIN-CLM-HK-PRD",  "daily"),
        ("glue_catalog.finance_base_db.fin_claims_base",          "base",     "standard", "STR-FIN-CLM-0001", "finance_base_db",     "ACE-DA-FIN-CLM-BASE-PRD",      "ACE-DA-FIN-CLM-TRANSFORM-PRD", "ACE-DA-FIN-CLM-HK-PRD",  "weekly"),
    ]

    # ── ERS: booking stream ───────────────────────────────────────────────────
    ers_bkg = [
        ("glue_catalog.ers_staging_db.ers_booking_stg",           "staging",  "critical", "STR-ERS-BKG-0001", "ers_staging_db",  "ACE-DA-ERS-BKG-INGEST-PRD",    "ACE-DA-ERS-BKG-INGEST-PRD",    "ACE-DA-ERS-BKG-HK-PRD",  "every_trigger"),
        ("glue_catalog.ers_datalake_db.ers_booking_dl",            "datalake", "critical", "STR-ERS-BKG-0001", "ers_datalake_db", "ACE-DA-ERS-BKG-TRANSFORM-PRD", "ACE-DA-ERS-BKG-INGEST-PRD",    "ACE-DA-ERS-BKG-HK-PRD",  "daily"),
        ("glue_catalog.ers_datalake_db.ers_inventory_dl",          "datalake", "standard", "STR-ERS-INV-0001", "ers_datalake_db", "ACE-DA-ERS-INV-TRANSFORM-PRD", "ACE-DA-ERS-INV-INGEST-PRD",    "ACE-DA-ERS-HK-PRD",      "daily"),
    ]

    # ── Membership: profile stream ────────────────────────────────────────────
    mbr = [
        ("glue_catalog.membership_staging_db.mbr_profile_stg",    "staging",  "standard", "STR-MBR-PRF-0001", "membership_staging_db", "ACE-DA-MBR-PRF-INGEST-PRD",    "ACE-DA-MBR-PRF-INGEST-PRD",    "ACE-DA-MBR-HK-PRD",  "daily"),
        ("glue_catalog.membership_staging_db.mbr_activity_stg",   "staging",  "low",      "STR-MBR-ACT-0001", "membership_staging_db", "ACE-DA-MBR-ACT-INGEST-PRD",    "ACE-DA-MBR-ACT-INGEST-PRD",    "ACE-DA-MBR-HK-PRD",  "weekly"),
    ]

    # ── Claims: flat structure (no master) ────────────────────────────────────
    clm = [
        ("glue_catalog.claims_staging_db.clm_incident_stg",       "staging",  "standard", "STR-CLM-INC-0001", "claims_staging_db", "ACE-DA-CLM-INC-INGEST-PRD",    "ACE-DA-CLM-INC-INGEST-PRD",    "ACE-DA-CLM-HK-PRD",  "daily"),
        ("glue_catalog.claims_staging_db.clm_settlement_stg",     "staging",  "standard", "STR-CLM-STL-0001", "claims_staging_db", "ACE-DA-CLM-STL-INGEST-PRD",    "ACE-DA-CLM-STL-INGEST-PRD",    "ACE-DA-CLM-HK-PRD",  "daily"),
    ]

    # ── Tables with issues (circuit breaker, disabled, ramp-up) ──────────────
    special = [
        ("glue_catalog.finance_datalake_db.fin_reconcile_dl",     "datalake", "low",      "STR-FIN-REC-0001", "finance_datalake_db", "ACE-DA-FIN-REC-TRANSFORM-PRD", "ACE-DA-FIN-REC-INGEST-PRD",    "ACE-DA-FIN-HK-PRD",  "weekly"),
        ("glue_catalog.ers_staging_db.ers_pricing_stg",           "staging",  "low",      "STR-ERS-PRC-0001", "ers_staging_db",      "ACE-DA-ERS-PRC-INGEST-PRD",    "ACE-DA-ERS-PRC-INGEST-PRD",    "ACE-DA-ERS-HK-PRD",  "daily"),
        ("glue_catalog.membership_staging_db.mbr_rewards_stg",    "staging",  "low",      "STR-MBR-RWD-0001", "membership_staging_db", "ACE-DA-MBR-RWD-INGEST-PRD",  "ACE-DA-MBR-RWD-INGEST-PRD",    "ACE-DA-MBR-HK-PRD",  "monthly"),
    ]

    all_groups = fin_aps + fin_claims + ers_bkg + mbr + clm + special

    hk_enabled_set = {t[0] for t in (fin_aps + fin_claims + ers_bkg[:2] + mbr[:1] + clm)}
    rampup_set     = {ers_bkg[2][0], mbr[1][0]}
    for i, (fqn, layer, tier, stream_id, db_name,
            pipeline_job, dep_job, hk_job, freq) in enumerate(all_groups):

        domain = fqn.split(".")[1].split("_")[0]

        hk_en    = 1 if fqn in hk_enabled_set else 0
        dry_until = None
        if fqn in rampup_set:
            dry_until = _date(-7 + 14)  # active ramp-up, expires in 7 days

        tables.append({
            "table_fqn":               fqn,
            "stream_id":               stream_id,
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
                    "stream_id":        tbl.get("stream_id", ""),
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
