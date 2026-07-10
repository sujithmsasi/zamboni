"""
Zamboni — Central Settings
All modules import from here. Never read os.environ directly elsewhere.
Values loaded from .env via python-dotenv.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root regardless of working directory.
# Looks for: <project_root>/.env  (one level up from config/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# ── Test mode ─────────────────────────────────────────────────────────────────
# Set ZAMBONI_TEST_MODE=true to run unit tests without a real .env file.
# All required env vars fall back to safe mock values in this mode.
_TEST = os.getenv("ZAMBONI_TEST_MODE", "false").lower() == "true"


def _req(key: str, mock: str = "s3://mock/") -> str:
    """Return env var or mock value in test mode; raises KeyError in production."""
    val = os.getenv(key)
    if val:
        return val
    if _TEST:
        return mock
    raise KeyError(
        f"Required env var {key!r} is not set. "
        "Copy .env.example to .env and fill in values. "
        "For tests, set ZAMBONI_TEST_MODE=true."
    )


# ── AWS ───────────────────────────────────────────────────────────────────────
AWS_REGION     = os.getenv("AWS_REGION", "us-west-2")
AWS_ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID", "")

# ── Athena ────────────────────────────────────────────────────────────────────
ATHENA_CATALOG        = os.getenv("ATHENA_CATALOG", "glue_catalog")
ATHENA_DATABASE       = os.getenv("ATHENA_DATABASE", "zamboni_catalog")
ATHENA_RESULTS_BUCKET = _req("ATHENA_RESULTS_BUCKET", "s3://mock-athena-results/")

ATHENA_WORKGROUPS = {
    "critical": os.getenv("ATHENA_WG_CRITICAL", "zamboni-critical"),
    "standard": os.getenv("ATHENA_WG_STANDARD", "zamboni-standard"),
    "low":      os.getenv("ATHENA_WG_LOW",      "zamboni-low"),
    "archival": os.getenv("ATHENA_WG_ARCHIVAL",  "zamboni-archival"),
    "app":      os.getenv("ATHENA_WG_APP",        "zamboni-app"),
}

# ── Metadata Tables ───────────────────────────────────────────────────────────
DOMAIN_REGISTRY_TABLE  = os.getenv(
    "DOMAIN_REGISTRY_TABLE",
    "glue_catalog.zamboni_catalog.domain_registry"
)
STREAM_REGISTRY_TABLE  = os.getenv(
    "STREAM_REGISTRY_TABLE",
    "glue_catalog.zamboni_catalog.stream_registry"
)
HK_CONFIG_TABLE        = os.getenv(
    "HK_CONFIG_TABLE",
    "glue_catalog.zamboni_catalog.hk_config"
)
EXECUTION_LOG_TABLE    = os.getenv(
    "EXECUTION_LOG_TABLE",
    "glue_catalog.zamboni_catalog.execution_log"
)
HOME_SNAPSHOT_TABLE = os.getenv(
    "ZAMBONI_HOME_SNAPSHOT_TABLE",
    "glue_catalog.zamboni_catalog.home_snapshot"
)
NONPROD_REGISTRY_TABLE = os.getenv(
    "NONPROD_REGISTRY_TABLE",
    "glue_catalog.zamboni_catalog.nonprod_registry"
)
AUDIT_LOG_TABLE = os.getenv("AUDIT_LOG_TABLE", "glue_catalog.zamboni_catalog.audit_log")
CONTROLM_JOBS_TABLE = os.getenv(
    "CONTROLM_JOBS_TABLE",
    "glue_catalog.zamboni_catalog.controlm_jobs"
)

# ── S3 ────────────────────────────────────────────────────────────────────────
STAGING_BUCKET          = _req("STAGING_BUCKET",          "s3://mock-staging/")
ARCHIVE_BUCKET          = _req("ARCHIVE_BUCKET",          "s3://mock-archive/")
ZAMBONI_METADATA_BUCKET = _req("ZAMBONI_METADATA_BUCKET", "s3://mock-metadata/")

# ── SNS ───────────────────────────────────────────────────────────────────────
SNS_ALERT_TOPIC_ARN    = _req("SNS_ALERT_TOPIC_ARN",    "arn:aws:sns:us-west-2:123456789012:mock-alerts")
SNS_GREENZONE_TOPIC_ARN= _req("SNS_GREENZONE_TOPIC_ARN","arn:aws:sns:us-west-2:123456789012:mock-greenzone")

# ── Engine Behaviour ──────────────────────────────────────────────────────────
DRY_RUN_DEFAULT           = os.getenv("DRY_RUN_DEFAULT", "true").lower() == "true"
LOG_LEVEL                 = os.getenv("LOG_LEVEL", "INFO")
MAX_CONCURRENT_PARTITIONS = int(os.getenv("MAX_CONCURRENT_PARTITIONS", "10"))
CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3"))

# ── Hard floors — never override these in config ──────────────────────────────
SNAPSHOT_MIN_FLOOR            = 30    # Zamboni safety floor (conservative buffer for SCD2)
SNAPSHOT_TRIVIAL_SKIP         = 5     # G9: skip VACUUM when snapshot_count < this
ANOMALOUS_COMMITS_WARN        = 50    # G10: warn if commits/day exceeds this
ANOMALOUS_COMMITS_BLOCK       = 100   # G10: block if commits/day exceeds this
VACUUM_MAX_ITERATIONS         = 3     # Max VACUUM iterations for bloated tables
VACUUM_ITERATION_SLEEP_SECS   = 30    # Sleep between VACUUM iterations
VACUUM_BLOAT_THRESHOLD        = 500   # expired_snapshots threshold for iterative VACUUM
ORPHAN_MIN_RETENTION_HOURS    = 48    # Never delete files newer than 48h

# ── Layers ────────────────────────────────────────────────────────────────────
VALID_LAYERS = ["staging", "datalake", "base", "master"]

# ── Tiers ─────────────────────────────────────────────────────────────────────
VALID_TIERS = ["critical", "standard", "low"]

# ── Environments ──────────────────────────────────────────────────────────────
VALID_ENVIRONMENTS = ["prod", "preprod", "dev", "test"]
NONPROD_ENVIRONMENTS = ["preprod", "dev", "test"]

# ── Local Mode (no AWS required) ─────────────────────────────────────────────
# Set ZAMBONI_LOCAL_MODE=true to run against a local SQLite database.
# Used for UI development and validation without AWS connectivity.
ZAMBONI_LOCAL_MODE = os.getenv("ZAMBONI_LOCAL_MODE", "false").lower() == "true"
ZAMBONI_LOCAL_DB   = os.getenv("ZAMBONI_LOCAL_DB", "zamboni_local.db")

# ── Control Plane (SQLite-primary for config/control tables) ────────────────
# stream_registry, hk_config, domain_registry, nonprod_registry, controlm_jobs
# are SQLite-primary in production, not just local mode: the API/UI write here
# first (fast, synchronous), and the engine reads the SAME file directly --
# safe because SQLite is the first point of write, so reads are fresh by
# construction. execution_log/audit_log/vacuum_audit and a handful of
# engine-owned stream_registry columns (aws_opt_*, last_execution_id,
# metadata_location, properties_synced) stay Athena-direct, untouched.
# See engine/core/control_plane.py. In ZAMBONI_LOCAL_MODE this resolves to
# ZAMBONI_LOCAL_DB instead (zero behavior change for local/demo).
# In production this is a bare filename by default for laptop use --
# override to a persistent path (e.g. /data/zamboni/zamboni_control.db) via
# the EC2 .env. A periodic background sync (scripts/control_plane_sync.py)
# pushes its current state to Athena for reporting/recovery/history.
ZAMBONI_CONTROL_PLANE_DB = os.getenv("ZAMBONI_CONTROL_PLANE_DB", "zamboni_control.db")


# ── Mode / Session Factory (Workstream A, Phase 1a — contracts.md §2) ───────
# get_mode() is the single source of truth for which backend a call should
# use. ZAMBONI_LOCAL_MODE (bool, above) is preserved for backward
# compatibility -- "local" here maps 1:1 onto it. aws_local is the
# laptop-demo mode (real AWS via SSO profile); aws_ec2 is the deployed
# instance-role mode (default when neither ZAMBONI_MODE nor
# ZAMBONI_LOCAL_MODE is set).
def get_mode() -> str:
    m = os.getenv("ZAMBONI_MODE")
    if m in ("local", "aws_local", "aws_ec2"):
        return m
    return "local" if os.getenv("ZAMBONI_LOCAL_MODE", "").lower() == "true" else "aws_ec2"


def get_boto3_session():
    """Return a boto3 Session appropriate for the current mode."""
    import boto3
    if get_mode() == "aws_local":
        return boto3.Session(profile_name=os.getenv("AWS_SSO_PROFILE", "prod-toolsgenai-sso"))
    return boto3.Session()  # instance role / env chain


# ── Maintenance Safety (Workstream A — contracts.md §2, §3.1) ───────────────
# Coordination primitives for the lock service, conflict detector, and Gate 0.
# ORPHAN_MIN_RETENTION_HOURS (above, =48) predates this phase and is asserted
# by 2 existing tests (test_settings.py, test_vacuum.py) but is not consumed
# by engine/operations/vacuum.py -- the real floor there is TBLPROPERTIES-
# driven. ORPHAN_MIN_AGE_HOURS_FLOOR below is the floor Gate 0/orchestrator
# actually clamps to (contracts.md D2). Both constants are kept side by side
# -- see contracts.md Conflict List item 3.
ORPHAN_MIN_AGE_HOURS_FLOOR   = int(os.getenv("ORPHAN_MIN_AGE_HOURS_FLOOR", "72"))   # hard clamp-up
ORPHAN_DEFAULT_AGE_HOURS     = int(os.getenv("ORPHAN_DEFAULT_AGE_HOURS", "96"))
SNAPSHOT_MIN_AGE_HOURS       = int(os.getenv("SNAPSHOT_MIN_AGE_HOURS", "24"))       # never expire younger
MAX_ORPHAN_DELETE_PCT        = int(os.getenv("MAX_ORPHAN_DELETE_PCT", "20"))        # abort above this
CONFLICT_CACHE_TTL_HOURS     = int(os.getenv("CONFLICT_CACHE_TTL_HOURS", "24"))
LOCK_TTL_MINUTES             = int(os.getenv("LOCK_TTL_MINUTES", "120"))
LOCK_HEARTBEAT_SECONDS       = int(os.getenv("LOCK_HEARTBEAT_SECONDS", "60"))
GATE0_OVERRIDE_MAX_HOURS     = int(os.getenv("GATE0_OVERRIDE_MAX_HOURS", "24"))
DDB_LOCK_TABLE               = os.getenv("DDB_LOCK_TABLE", "zamboni_maintenance_locks")

# ── Orchestrator (Workstream A, Phase 1b — contracts.md §5 / §5-A) ───────────
# Rollback lever: false preserves the pre-orchestrator per-op flow in
# hk_engine.py untouched. Default true once Phase 1b ships.
ORCHESTRATED_MAINTENANCE = os.getenv("ORCHESTRATED_MAINTENANCE", "true").lower() == "true"
VACUUM_AUDIT_TABLE = os.getenv(
    "VACUUM_AUDIT_TABLE",
    "glue_catalog.zamboni_catalog.vacuum_audit"
)


def clamp_orphan_age(policy_hours: int) -> int:
    """Clamp a policy-configured orphan age (hours) up to the hard floor."""
    return max(int(policy_hours), ORPHAN_MIN_AGE_HOURS_FLOOR)


# ── Cost estimation (Health Dashboard "storage saved" widget) ────────────────
# Flat-rate estimate, same convention as the $5/TB Athena scan-cost estimate
# api/services/executions_svc.py::costs() already uses -- illustrative, not a
# live billing figure. us-east-1 S3 Standard list price as of this writing.
S3_STANDARD_USD_PER_GB_MONTH = float(os.getenv("S3_STANDARD_USD_PER_GB_MONTH", "0.023"))

# ── Execution Log Write Mode (v2) ─────────────────────────────────────────────
# Controls how engines write to the Iceberg execution_log table:
#   parquet — Batch Parquet to S3 + add_files (preferred, fast)
#   insert  — Per-row Athena INSERT (legacy, slow)
#   both    — Try Parquet first, fall back to INSERT on failure
#   auto    — (default) Use Parquet if pandas/pyarrow available, else INSERT
# Athena query timeout -- queries exceeding this are cancelled automatically
# Set to 0 to disable. Recommended: 1800 (30 min) for prod.
ATHENA_QUERY_TIMEOUT_SECONDS: int = int(os.getenv("ATHENA_QUERY_TIMEOUT_SECONDS", "1800"))

# Glue compaction job timeout -- engine/operations/compaction.py's polling
# loop was unbounded (a stuck Glue job hung the worker thread forever, no
# way to time out or auto-cancel). Same convention as the Athena timeout
# above: set to 0 to disable, recommended 3600 (1h) for prod.
GLUE_JOB_TIMEOUT_SECONDS: int = int(os.getenv("GLUE_JOB_TIMEOUT_SECONDS", "3600"))

EXECUTION_LOG_MODE = os.getenv("EXECUTION_LOG_MODE", "auto").lower()


# ── CloudTrail (Lifecycle Engine activity signals) ────────────────────────────
# Set CLOUDTRAIL_TABLE if you have CloudTrail logs in Athena.
# If not set, activity signals fall back to Glue table CreateTime only.
# Format: glue_catalog.database.table
CLOUDTRAIL_TABLE     = os.getenv("CLOUDTRAIL_TABLE", "")
CLOUDTRAIL_LOOKBACK_DAYS = int(os.getenv("CLOUDTRAIL_LOOKBACK_DAYS", "90"))

# ── Streamlit App ─────────────────────────────────────────────────────────────
APP_PORT = int(os.getenv("APP_PORT", "8501"))
APP_ENV  = os.getenv("APP_ENV", "dev")
