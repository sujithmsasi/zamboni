"""
Zamboni -- Scale Test Seed
Generates ~5,000 tables per domain across 5 domains = ~25,000 tables total.
Tests dropdown performance, grid pagination, and query performance at scale.

Usage:
    python scripts/seed_scale_test.py           # full 25K tables
    python scripts/seed_scale_test.py --mini    # 500 tables (quick test)

WARNING: replaces stream_registry and related data. Re-run seed_local_db.py
to restore normal seed data.
"""
from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ZAMBONI_LOCAL_MODE", "true")
os.environ.setdefault("ZAMBONI_TEST_MODE",  "true")

MINI = "--mini" in sys.argv
TABLES_PER_DOMAIN = 100 if MINI else 1000  # 5 domains × = 500 or 5000

print(f"Scale test: {'MINI' if MINI else 'FULL'} — "
      f"{TABLES_PER_DOMAIN} tables/domain × 5 domains = "
      f"{TABLES_PER_DOMAIN * 5:,} total")

from engine.utils.local_db import get_connection, insert_rows  # noqa: E402

conn = get_connection()

# ── Truncate existing registry data ──────────────────────────────────────────
conn.execute("DELETE FROM stream_registry")
conn.execute("DELETE FROM hk_config")
conn.execute("DELETE FROM execution_log")
conn.commit()
print("Cleared existing stream_registry, hk_config, execution_log")


def _now(offset_days: int = 0) -> str:
    dt = datetime.now(UTC) - timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Domain definitions ────────────────────────────────────────────────────────
DOMAINS = {
    "finance": {
        "dbs":     ["finance_staging_db","finance_datalake_db","finance_base_db","finance_master_db"],
        "streams": ["APS","CLM","REC","PAY","INV","RFD","ACH","WRE"],
        "ci_base": "CI-10300",
    },
    "ers": {
        "dbs":     ["ers_staging_db","ers_datalake_db","ers_base_db"],
        "streams": ["BKG","INV","PRC","VEH","MBR","RSV","ROD","TRP"],
        "ci_base": "CI-20300",
    },
    "membership": {
        "dbs":     ["membership_staging_db","membership_datalake_db","membership_base_db"],
        "streams": ["PRF","ACT","RWD","TRN","SUB","PAY","ENR","LYL"],
        "ci_base": "CI-30300",
    },
    "claims": {
        "dbs":     ["claims_staging_db","claims_datalake_db","claims_base_db"],
        "streams": ["INC","STL","RPR","PHY","MED","VEH","PRO","LIA"],
        "ci_base": "CI-40300",
    },
    "travel": {
        "dbs":     ["travel_staging_db","travel_datalake_db","travel_base_db"],
        "streams": ["ITN","HTL","FLT","CAR","PKG","INS","LYL","BKG"],
        "ci_base": "CI-50300",
    },
}

LAYERS     = ["staging", "datalake", "base", "master"]
LAYER_DB   = {"staging":"_staging_db","datalake":"_datalake_db","base":"_base_db","master":"_master_db"}
TIERS      = ["critical","standard","low"]
TIER_DIST  = [0.2, 0.5, 0.3]
CADENCES   = ["daily","daily","daily","weekly","every_trigger"]
STRATEGIES = ["binpack","binpack","binpack","sort","zorder"]
ENGINES    = ["athena","athena","athena","glue"]

registry_rows = []
config_rows   = []
exec_rows     = []

total = 0
for domain, meta in DOMAINS.items():
    streams = meta["streams"]
    ci_offset = int(meta["ci_base"].split("-")[1])

    for i in range(TABLES_PER_DOMAIN):
        stream_code = streams[i % len(streams)]
        seq         = (i // len(streams)) + 1
        stream_id   = f"STR-{domain[:3].upper()}-{stream_code}-{seq:04d}"

        layer       = LAYERS[i % len(LAYERS)]
        db_suffix   = LAYER_DB.get(layer, "_staging_db")
        db_name     = f"{domain}{db_suffix}"

        # Generate realistic table names
        prefixes = {
            "staging":  ["stg","raw","ingest","land"],
            "datalake": ["dl","lake","curated","clean"],
            "base":     ["base","scd2","hist","cdc"],
            "master":   ["mstr","master","agg","mart"],
        }
        prefix   = random.choice(prefixes[layer])
        table_nm = f"{domain[:3]}_{stream_code.lower()}_{prefix}_{i:04d}"
        fqn      = f"glue_catalog.{db_name}.{table_nm}"

        tier     = random.choices(TIERS, TIER_DIST)[0]
        cadence  = random.choice(CADENCES)
        hk_en    = 1 if i % 3 != 0 else 0  # 67% enabled
        strategy = random.choice(STRATEGIES)
        engine   = "glue" if strategy in ("sort","zorder") else "athena"
        ci_num   = f"CI-{ci_offset + i}"

        pipeline_job  = f"ACE-DA-{domain[:3].upper()}-{stream_code}-INGEST-PRD"
        hk_job        = f"ACE-DA-{domain[:3].upper()}-HK-PRD"

        registry_rows.append({
            "table_fqn":              fqn,
            "stream_id":              stream_id,
            "domain":                 domain,
            "layer":                  layer,
            "tier":                   tier,
            "table_format":           "iceberg",
            "environment":            "prod",
            "owner_email":            f"da-{domain}@company.com",
            "ci_number":              ci_num,
            "hk_enabled":             hk_en,
            "dry_run_until":          None,
            "force_run":              0,
            "dependent_job_name":     pipeline_job,
            "dependent_job_type":     "glue",
            "controlm_pipeline_job":  pipeline_job,
            "controlm_hk_job":        hk_job,
            "dependent_on_controlm_job": pipeline_job,
            "archive_enabled":        1 if layer == "staging" else 0,
            "archive_retention_days": 30 if layer == "staging" else None,
            "archive_bucket":         None,
            "lifecycle_enabled":      0,
            "processing_cadence":     cadence,
            "properties_synced":      1,
            "last_execution_id":      None,
            "registered_by":          "seed:scale_test",
            "registered_at":          _now(random.randint(30,365)),
            "updated_at":             _now(random.randint(0,30)),
            "database_name":          db_name,
            "owner_name":             "",
            "notes":                  "",
            "partition_type":         "date",
        })

        config_rows.append({
            "table_fqn":                       fqn,
            "policy_template":                 "STAGING_DEFAULT" if layer=="staging" else
                                               "DATALAKE_DEFAULT" if layer=="datalake" else
                                               "BASE_SCD2" if layer=="base" else "MASTER_DEFAULT",
            "compaction_strategy":             strategy,
            "compaction_target_file_size_mb":  128 if layer in ("staging","datalake") else 256,
            "compaction_engine":               engine,
            "sort_order_cols":                 "partition_date" if strategy in ("sort","zorder") else None,
            "snapshot_retention_days":         7 if tier=="critical" else 14 if tier=="standard" else 30,
            "snapshot_min_to_keep":            30,
            "orphan_file_retention_days":      2,
            "orphan_cleanup_cadence_days":     7,
            "run_frequency":                   cadence,
            "partition_column":                "partition_date",
            "partition_filter_days":           90,
            "window_config":                   '{"type":"post_batch","timezone":"America/Los_Angeles","delay_minutes":30,"duration_hours":4,"blackout_hours":[6,7,8,9,18,19,20,21]}',
            "manually_overridden":             0,
            "override_notes":                  None,
            "created_at":                      _now(random.randint(30,365)),
            "updated_at":                      _now(random.randint(0,30)),
            "partition_type":                  "date",
        })

        # Add a few execution log rows per table
        if random.random() < 0.7 and hk_en:
            for _ in range(random.randint(1, 3)):
                day = random.randint(0, 29)
                started = _now(day)
                status  = random.choices(
                    ["SUCCESS","SUCCESS","SUCCESS","SKIPPED","FAILURE"],
                    [0.6, 0.1, 0.1, 0.15, 0.05]
                )[0]
                exec_rows.append({
                    "execution_id":       str(uuid.uuid4()),
                    "run_id":             f"hk-{_now(day)[:10]}-{uuid.uuid4().hex[:6]}",
                    "engine":             "hk",
                    "operation":          "hk_run",
                    "table_fqn":          fqn,
                    "stream_id":          stream_id,
                    "domain":             domain,
                    "layer":              layer,
                    "tier":              tier,
                    "environment":        "prod",
                    "status":             status,
                    "dry_run":            0,
                    "skip_reason":        "SKIP_NOT_DUE" if status=="SKIPPED" else None,
                    "error_message":      "Athena timeout" if status=="FAILURE" else None,
                    "started_at":         started,
                    "completed_at":       started,
                    "duration_seconds":   round(random.uniform(30,600),1),
                    "snapshots_before":   random.randint(30,200),
                    "snapshots_after":    random.randint(10,30),
                    "snapshots_expired":  random.randint(10,100),
                    "files_compacted":    random.randint(50,2000),
                    "bytes_rewritten":    random.randint(100_000_000, 5_000_000_000),
                    "orphan_files_deleted":random.randint(0,50),
                    "bytes_archived":     None,
                    "bytes_scanned":      random.randint(500_000_000,50_000_000_000),
                    "athena_query_id":    f"query-{uuid.uuid4().hex[:12]}",
                    "execution_date":     started[:10],
                    "rows_archived":      0,
                    "vacuum_iterations":  1,
                    "oldest_snapshot_id": None,
                    "newest_snapshot_id": None,
                })

        total += 1

print(f"\nInserting {len(registry_rows):,} tables...")
BATCH = 500
for start in range(0, len(registry_rows), BATCH):
    insert_rows("stream_registry", registry_rows[start:start+BATCH])
    if start % 2000 == 0 and start > 0:
        print(f"  {start:,}/{len(registry_rows):,}...")

print(f"Inserting {len(config_rows):,} HK configs...")
for start in range(0, len(config_rows), BATCH):
    insert_rows("hk_config", config_rows[start:start+BATCH])

print(f"Inserting {len(exec_rows):,} execution rows...")
for start in range(0, len(exec_rows), BATCH):
    insert_rows("execution_log", exec_rows[start:start+BATCH])

conn.execute("UPDATE home_snapshot SET total_tables = ?, hk_enabled_count = ? WHERE 1",
             (len(registry_rows), sum(1 for r in registry_rows if r["hk_enabled"])))
conn.commit()

print(f"""
{'='*55}
Scale test seed complete!
  Tables:         {len(registry_rows):>8,}
  HK Configs:     {len(config_rows):>8,}
  Execution rows: {len(exec_rows):>8,}

Per domain breakdown:
{'='*55}""")

for domain in DOMAINS:
    n = sum(1 for r in registry_rows if r["domain"] == domain)
    h = sum(1 for r in registry_rows if r["domain"] == domain and r["hk_enabled"])
    print(f"  {domain:<12} {n:>6,} tables  {h:>5,} HK-enabled")

print(f"""
Launch: .\\run_local.bat
{'='*55}""")
