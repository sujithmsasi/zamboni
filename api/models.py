"""
Zamboni API -- Pydantic v2 models (contracts.md §6).

Envelope shape is LOCKED: {"data": ..., "pagination": {page,size,total}|null,
"error": {code,message}|null}. Mutations return MutationResult
{success, dry_run, audit_id}.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

# ── Envelope ──────────────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str


class Pagination(BaseModel):
    page: int
    size: int
    total: int


def _clean(obj: Any) -> Any:
    """
    Recursively make pandas/numpy query results JSON-safe: NaN/NaT -> None,
    numpy scalars -> native Python, Timestamp/date -> ISO string. Runs on
    every envelope() call so services can pass raw DataFrame.to_dict()
    output straight through without each one reimplementing this.
    """
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            return _clean(obj.item())
    except ImportError:
        pass
    try:
        import pandas as pd
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if obj is not None and pd.isna(obj):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    return obj


def envelope(data: Any, pagination: Pagination | dict | None = None, error: ErrorDetail | dict | None = None) -> dict:
    """Build the locked {data, pagination, error} envelope dict."""
    return {
        "data": _clean(data),
        "pagination": pagination.model_dump() if isinstance(pagination, Pagination) else pagination,
        "error": error.model_dump() if isinstance(error, ErrorDetail) else error,
    }


class MutationResult(BaseModel):
    success: bool
    dry_run: bool
    audit_id: str | None = None


# ── tables ────────────────────────────────────────────────────────────────────

class RegisterTableRequest(BaseModel):
    table_fqn: str
    domain: str
    layer: str
    tier: str
    environment: str = "prod"
    table_format: str = "iceberg"
    owner_email: str = ""
    ci_number: str = ""
    hk_enabled: bool = False
    archive_enabled: bool = False
    archive_retention_days: int | None = None
    notes: str = ""
    controlm_pipeline_job: str | None = None
    controlm_hk_job: str | None = None
    dependent_on_controlm_job: str | None = None
    controlm_job_start_time: str = "02:00"
    controlm_expected_duration_min: int = 0
    dependent_job_type: str = "controlm"
    dry_run: bool = True


class UpdateTableRequest(BaseModel):
    """Partial update -- only provided fields are written."""
    domain: str | None = None
    layer: str | None = None
    tier: str | None = None
    owner_email: str | None = None
    ci_number: str | None = None
    hk_enabled: bool | None = None
    archive_enabled: bool | None = None
    lifecycle_enabled: bool | None = None
    processing_cadence: str | None = None
    controlm_pipeline_job: str | None = None
    controlm_hk_job: str | None = None
    dependent_on_controlm_job: str | None = None
    dependent_job_type: str | None = None
    controlm_job_start_time: str | None = None
    controlm_expected_duration_min: int | None = None
    dry_run: bool = True


class BulkControlMRequest(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    set_fields: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


# ── policies ──────────────────────────────────────────────────────────────────

class PolicyUpdateRequest(BaseModel):
    gate1_enabled: bool | None = None
    gate2_enabled: bool | None = None
    gate3_enabled: bool | None = None
    snapshot_retention_days: int | None = None
    snapshot_min_to_keep: int | None = None
    orphan_file_retention_days: int | None = None
    orphan_cleanup_cadence_days: int | None = None
    run_frequency: str | None = None
    compaction_strategy: str | None = None
    compaction_engine: str | None = None
    compaction_target_file_size_mb: int | None = None
    sort_order_cols: str | None = None
    partition_column: str | None = None
    partition_type: str | None = None
    window_config: dict[str, Any] | None = None
    override_notes: str | None = None
    dry_run: bool = True


class TemplateUpdateRequest(BaseModel):
    description: str | None = None
    compaction_strategy: str | None = None
    compaction_engine: str | None = None
    compaction_target_file_size_mb: int | None = None
    snapshot_retention_days: int | None = None
    snapshot_min_to_keep: int | None = None
    orphan_file_retention_days: int | None = None
    run_frequency: str | None = None
    gate1_enabled: bool | None = None
    gate2_enabled: bool | None = None
    gate3_enabled: bool | None = None
    window_config: dict[str, Any] | None = None
    dry_run: bool = True


class TemplateCreateRequest(BaseModel):
    """> ADDED (Phase 5a): POST /api/templates has no contracts.md §6 lock --
    see api/services/policies_svc.py::create_template()'s note."""
    name: str
    description: str = ""
    compaction_strategy: str = "binpack"
    compaction_engine: str = "athena"
    compaction_target_file_size_mb: int = 128
    snapshot_retention_days: int = 7
    snapshot_min_to_keep: int = 2
    orphan_file_retention_days: int = 2
    run_frequency: str = "daily"
    gate1_enabled: bool = False
    gate2_enabled: bool = True
    gate3_enabled: bool = True
    window_config: dict[str, Any] | None = None
    dry_run: bool = True


class TemplateApplyRequest(BaseModel):
    domain: str | None = None
    layer: str | None = None
    tier: str | None = None
    skip_overridden: bool = True
    dry_run: bool = True


# ── gates ─────────────────────────────────────────────────────────────────────

class GatesUpdateRequest(BaseModel):
    gate1_enabled: bool | None = None
    gate2_enabled: bool | None = None
    gate3_enabled: bool | None = None
    gate0_override_until: str | None = None
    gate0_override_reason: str | None = None
    dry_run: bool = True


class ConflictsRescanRequest(BaseModel):
    fqns: list[str] | None = None


# ── lifecycle ─────────────────────────────────────────────────────────────────

class NonprodExemptRequest(BaseModel):
    fqns: list[str]
    reason: str
    dry_run: bool = True


class NonprodClaimRequest(BaseModel):
    fqns: list[str]
    reason: str
    dry_run: bool = True


# ── domains (> ADDED Phase 4 — no domains section existed in contracts §6) ────

class RegisterDomainRequest(BaseModel):
    domain_name: str
    display_name: str
    owner_email: str
    description: str = ""
    owner_name: str = ""
    team_name: str = ""
    ci_number: str = ""
    archive_enabled: bool = True
    hot_retention_days: int = 30
    archive_duration_days: int = 365
    stale_threshold_days: int = 60
    auto_delete_after_days: int = 120
    is_active: bool = True
    notes: str = ""
    dry_run: bool = True


class UpdateDomainRequest(BaseModel):
    display_name: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    team_name: str | None = None
    ci_number: str | None = None
    archive_enabled: bool | None = None
    hot_retention_days: int | None = None
    archive_duration_days: int | None = None
    stale_threshold_days: int | None = None
    auto_delete_after_days: int | None = None
    is_active: bool | None = None
    digest_enabled: bool | None = None
    digest_email: str | None = None
    notes: str | None = None
    dry_run: bool = True


# ── controlm ──────────────────────────────────────────────────────────────────

class JobUpsertRequest(BaseModel):
    job_name: str
    job_type: str = "controlm"
    domain: str = ""
    description: str = ""
    expected_start_time: str = ""
    expected_duration_min: int = 0
    job_frequency: str = ""


# ── settings ──────────────────────────────────────────────────────────────────

class SettingsUpdateRequest(BaseModel):
    settings: dict[str, Any]
    dry_run: bool = True


class EscalationUpsertRequest(BaseModel):
    key: str
    entry: dict[str, Any]
    dry_run: bool = True
