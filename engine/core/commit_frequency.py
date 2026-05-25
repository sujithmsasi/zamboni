"""
Zamboni -- Commit Frequency Analysis
Queries $snapshots to determine table commit rate and classify
into HIGH / MEDIUM / LOW vacuum tier.

Used by:
  - property_sync.apply_vacuum_properties() — to set correct retention
  - health_checker._check_snapshots()       — to flag anomalous rates (G10)

Tier thresholds (from IceForge HK specification):
  HIGH:   > 48 commits/day  → 7-day  retention, max_metadata_files=90
  MEDIUM: 12-48 commits/day → 14-day retention, max_metadata_files=90
  LOW:    < 12 commits/day  → 30-day retention, max_metadata_files=100
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.utils.athena_client import read_sql
from engine.utils.logger import get_logger

log = get_logger(__name__)

# ── Tier definitions ──────────────────────────────────────────────────────────

COMMIT_TIER_HIGH   = "HIGH"
COMMIT_TIER_MEDIUM = "MEDIUM"
COMMIT_TIER_LOW    = "LOW"

TIER_THRESHOLDS = {
    # (min_commits_per_day, tier)
    COMMIT_TIER_HIGH:   48,
    COMMIT_TIER_MEDIUM: 12,
}

TIER_PROPERTIES = {
    COMMIT_TIER_HIGH: {
        "vacuum_max_snapshot_age_seconds":   604800,    # 7 days
        "vacuum_min_snapshots_to_keep":      2,
        "vacuum_max_metadata_files_to_keep": 90,
        "write_target_data_file_size_bytes": 268435456, # 256 MB
    },
    COMMIT_TIER_MEDIUM: {
        "vacuum_max_snapshot_age_seconds":   1209600,   # 14 days
        "vacuum_min_snapshots_to_keep":      2,
        "vacuum_max_metadata_files_to_keep": 90,
        "write_target_data_file_size_bytes": 268435456,
    },
    COMMIT_TIER_LOW: {
        "vacuum_max_snapshot_age_seconds":   2592000,   # 30 days
        "vacuum_min_snapshots_to_keep":      2,
        "vacuum_max_metadata_files_to_keep": 100,
        "write_target_data_file_size_bytes": 268435456,
    },
}


@dataclass
class CommitStats:
    """Commit frequency stats for a single table."""
    table_fqn:        str
    commits_7d:       int     = 0
    commits_per_day:  float   = 0.0
    commit_tier:      str     = COMMIT_TIER_LOW
    anomalous:        bool    = False    # G10: > ANOMALOUS_COMMITS_WARN
    pipeline_anomaly: bool    = False    # G10: > ANOMALOUS_COMMITS_BLOCK
    error:            str     = ""

    @property
    def vacuum_properties(self) -> dict:
        """Return the correct TBLPROPERTIES for this table's commit tier."""
        return TIER_PROPERTIES[self.commit_tier]


def get_commit_stats(
    table_fqn: str,
    workgroup: str = "standard",
    lookback_days: int = 7,
) -> CommitStats:
    """
    Query $snapshots to calculate commits/day and classify tier.
    Falls back to LOW tier on any error (safe default).

    Args:
        table_fqn:     Full table FQN e.g. glue_catalog.finance_db.fin_payment
        workgroup:     Athena workgroup to use
        lookback_days: How many days to look back for rate calculation

    Returns:
        CommitStats with tier, rate, and anomaly flags
    """
    from config.settings import ANOMALOUS_COMMITS_BLOCK, ANOMALOUS_COMMITS_WARN
    from engine.utils.partition_utils import parse_table_fqn

    stats = CommitStats(table_fqn=table_fqn)

    try:
        _, database, table = parse_table_fqn(table_fqn)

        sql = f"""
            SELECT
                COUNT(*) AS commits_in_window,
                MIN(made_current_at) AS oldest_in_window,
                MAX(made_current_at) AS newest_in_window
            FROM "glue_catalog"."{database}"."{table}$snapshots"
            WHERE made_current_at >= NOW() - INTERVAL '{lookback_days}' DAY
        """

        df = read_sql(sql, workgroup=workgroup, database=database)

        if df.empty or df.iloc[0]["commits_in_window"] is None:
            log.debug("commit_frequency.no_data", table_fqn=table_fqn)
            return stats

        row                = df.iloc[0]
        commits_in_window  = int(row["commits_in_window"])
        commits_per_day    = round(commits_in_window / lookback_days, 2)

        stats.commits_7d      = commits_in_window
        stats.commits_per_day = commits_per_day
        stats.commit_tier     = _classify_tier(commits_per_day)
        stats.anomalous       = commits_per_day > ANOMALOUS_COMMITS_WARN
        stats.pipeline_anomaly = commits_per_day > ANOMALOUS_COMMITS_BLOCK

        if stats.pipeline_anomaly:
            log.error(
                "commit_frequency.pipeline_anomaly",
                table_fqn=table_fqn,
                commits_per_day=commits_per_day,
                threshold=ANOMALOUS_COMMITS_BLOCK,
                action="VACUUM alone cannot solve this — fix at pipeline level",
            )
        elif stats.anomalous:
            log.warning(
                "commit_frequency.anomalous_rate",
                table_fqn=table_fqn,
                commits_per_day=commits_per_day,
                threshold=ANOMALOUS_COMMITS_WARN,
                recommendation="Review pipeline for retry storms or micro-batching",
            )
        else:
            log.debug(
                "commit_frequency.classified",
                table_fqn=table_fqn,
                commits_per_day=commits_per_day,
                tier=stats.commit_tier,
            )

    except Exception as e:
        log.warning(
            "commit_frequency.query_failed",
            table_fqn=table_fqn,
            error=str(e),
        )
        stats.error = str(e)
        # Fail safe — default to LOW (most conservative retention)

    return stats


def classify_tier_from_config(hk_config: dict) -> str:
    """
    Classify tier from hk_config.snapshot_retention_days when
    $snapshots query is unavailable (e.g. local mode, first run).

    Maps retention days → commit tier:
      <= 7 days  → HIGH
      <= 14 days → MEDIUM
      > 14 days  → LOW
    """
    retention = int(hk_config.get("snapshot_retention_days") or 7)
    if retention <= 7:
        return COMMIT_TIER_HIGH
    if retention <= 14:
        return COMMIT_TIER_MEDIUM
    return COMMIT_TIER_LOW


def _classify_tier(commits_per_day: float) -> str:
    if commits_per_day > TIER_THRESHOLDS[COMMIT_TIER_HIGH]:
        return COMMIT_TIER_HIGH
    if commits_per_day > TIER_THRESHOLDS[COMMIT_TIER_MEDIUM]:
        return COMMIT_TIER_MEDIUM
    return COMMIT_TIER_LOW
