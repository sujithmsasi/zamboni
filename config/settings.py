"""
Zamboni — Central Settings
All modules import from here. Never read os.environ directly elsewhere.
Values loaded from .env via python-dotenv.
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ── AWS ───────────────────────────────────────────────────────────────────────
AWS_REGION     = os.getenv("AWS_REGION", "us-west-2")
AWS_ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID", "")

# ── Athena ────────────────────────────────────────────────────────────────────
ATHENA_CATALOG        = os.getenv("ATHENA_CATALOG", "glue_catalog")
ATHENA_DATABASE       = os.getenv("ATHENA_DATABASE", "zamboni_catalog")
ATHENA_RESULTS_BUCKET = os.environ["ATHENA_RESULTS_BUCKET"]

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
STAGING_BUCKET          = os.environ["STAGING_BUCKET"]
ARCHIVE_BUCKET          = os.environ["ARCHIVE_BUCKET"]
ZAMBONI_METADATA_BUCKET = os.environ["ZAMBONI_METADATA_BUCKET"]

# ── SNS ───────────────────────────────────────────────────────────────────────
SNS_ALERT_TOPIC_ARN    = os.environ["SNS_ALERT_TOPIC_ARN"]
SNS_GREENZONE_TOPIC_ARN= os.environ["SNS_GREENZONE_TOPIC_ARN"]

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

# ── Streamlit App ─────────────────────────────────────────────────────────────
APP_PORT = int(os.getenv("APP_PORT", "8501"))
APP_ENV  = os.getenv("APP_ENV", "dev")
