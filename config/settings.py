"""
Zamboni — Central Settings
All modules import from here. Never read os.environ directly elsewhere.
Values loaded from .env via python-dotenv.
"""
import os
from dotenv import load_dotenv

load_dotenv()

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
NONPROD_REGISTRY_TABLE = os.getenv(
    "NONPROD_REGISTRY_TABLE",
    "glue_catalog.zamboni_catalog.nonprod_registry"
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
SNAPSHOT_MIN_FLOOR            = 30    # Always retain at least 30 snapshots
ORPHAN_MIN_RETENTION_HOURS    = 48    # Never delete files newer than 48h

# ── Layers ────────────────────────────────────────────────────────────────────
VALID_LAYERS = ["staging", "datalake", "base", "master"]

# ── Tiers ─────────────────────────────────────────────────────────────────────
VALID_TIERS = ["critical", "standard", "low"]

# ── Environments ──────────────────────────────────────────────────────────────
VALID_ENVIRONMENTS = ["prod", "preprod", "dev", "test"]
NONPROD_ENVIRONMENTS = ["preprod", "dev", "test"]

# ── Execution Log Write Mode (v2) ─────────────────────────────────────────────
# Controls how engines write to the Iceberg execution_log table:
#   parquet — Batch Parquet to S3 + add_files (preferred, fast)
#   insert  — Per-row Athena INSERT (legacy, slow)
#   both    — Try Parquet first, fall back to INSERT on failure
#   auto    — (default) Use Parquet if pandas/pyarrow available, else INSERT
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
