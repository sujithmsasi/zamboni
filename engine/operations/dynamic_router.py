"""
Zamboni — Dynamic Router
Selects Glue worker type and execution class based on:
  - Table file metrics (size + file count from health_checker)
  - Table tier (critical → STANDARD, standard/low → FLEX)

This replaces hardcoded worker configs — the data decides.
"""
from dataclasses import dataclass

from engine.utils.logger import get_logger

log = get_logger(__name__)

# ── Worker Type Thresholds ────────────────────────────────────────────────────
# Based on total_size_gb and total_files from health_checker
WORKER_THRESHOLDS = [
    # (max_size_gb, max_files, worker_type, num_workers)
    (10,   1_000,  "G.1X", 2),
    (50,   5_000,  "G.2X", 3),
    (100,  10_000, "G.2X", 5),
    (250,  25_000, "G.4X", 8),
    (500,  50_000, "G.4X", 12),
]
# Anything above the last threshold
MAX_WORKER_TYPE     = "G.4X"
MAX_NUM_WORKERS     = 20

# ── Execution Class by Tier ───────────────────────────────────────────────────
TIER_EXECUTION_CLASS = {
    "critical": "STANDARD",
    "standard": "FLEX",
    "low":      "FLEX",
}


@dataclass
class RoutingDecision:
    """Result of dynamic routing for a Glue compaction job."""
    worker_type:     str
    num_workers:     int
    execution_class: str
    reason:          str


def route(
    tier: str,
    total_size_gb: float,
    total_files: int,
) -> RoutingDecision:
    """
    Select Glue worker type and execution class dynamically.

    Args:
        tier:          Table tier — critical | standard | low
        total_size_gb: Total data size in GB (from health_checker)
        total_files:   Total file count (from health_checker)

    Returns:
        RoutingDecision with worker_type, num_workers, execution_class, reason
    """
    # ── Worker type selection ──────────────────────────────────────────────────
    worker_type  = MAX_WORKER_TYPE
    num_workers  = MAX_NUM_WORKERS

    for max_gb, max_files, wtype, nworkers in WORKER_THRESHOLDS:
        if total_size_gb <= max_gb and total_files <= max_files:
            worker_type = wtype
            num_workers = nworkers
            break

    # ── Execution class selection ──────────────────────────────────────────────
    execution_class = TIER_EXECUTION_CLASS.get(tier, "FLEX")

    reason = (
        f"size={total_size_gb:.1f}GB, files={total_files} → "
        f"{worker_type} x{num_workers} workers | "
        f"tier={tier} → {execution_class}"
    )

    log.info(
        "dynamic_router.decision",
        tier=tier,
        total_size_gb=total_size_gb,
        total_files=total_files,
        worker_type=worker_type,
        num_workers=num_workers,
        execution_class=execution_class,
    )

    return RoutingDecision(
        worker_type=worker_type,
        num_workers=num_workers,
        execution_class=execution_class,
        reason=reason,
    )


def describe_thresholds() -> list[dict]:
    """Return routing thresholds as a list of dicts — used by Streamlit UI."""
    rows = []
    for max_gb, max_files, wtype, nworkers in WORKER_THRESHOLDS:
        rows.append({
            "max_size_gb":  max_gb,
            "max_files":    max_files,
            "worker_type":  wtype,
            "num_workers":  nworkers,
        })
    rows.append({
        "max_size_gb":  "unlimited",
        "max_files":    "unlimited",
        "worker_type":  MAX_WORKER_TYPE,
        "num_workers":  MAX_NUM_WORKERS,
    })
    return rows
