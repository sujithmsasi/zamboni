"""
Unit tests for engine/core/recovery.py (Workstream A / Phase 1c).
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

import engine.core.recovery as recovery
from engine.core.recovery import (
    ORPHAN_REFUSAL_REASON,
    get_rollback_candidates,
    rollback_metadata,
    validate_rollback_target,
)

FQN = "glue_catalog.finance_master_db.fin_payment_master"


# ══════════════════════════════════════════════════════════════════════════════
#  get_rollback_candidates
# ══════════════════════════════════════════════════════════════════════════════

def test_candidates_ordering_and_shape(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)

    df = pd.DataFrame([
        {
            "run_id": "run-2", "operation": "optimize", "status": "SUCCESS",
            "integrity_status": "VERIFIED",
            "metadata_location_before": "s3://b/mid.json",
            "metadata_location_after": "s3://b/cur.json",
            "snapshot_id_before": 2, "snapshot_id_after": 3,
            "started_at": "2026-07-04 10:00:00", "completed_at": "2026-07-04 10:01:00",
        },
        {
            "run_id": "run-1", "operation": "vacuum", "status": "SUCCESS",
            "integrity_status": "VERIFIED",
            "metadata_location_before": "s3://b/old.json",
            "metadata_location_after": "s3://b/mid.json",
            "snapshot_id_before": 1, "snapshot_id_after": 2,
            "started_at": "2026-07-02 10:00:00", "completed_at": "2026-07-02 10:01:00",
        },
    ])
    monkeypatch.setattr(recovery, "read_sql", lambda sql, **kw: df)

    candidates = get_rollback_candidates(FQN)
    assert len(candidates) == 2
    assert candidates[0]["run_id"] == "run-2"
    assert candidates[0]["metadata_location_before"] == "s3://b/mid.json"


def test_candidates_empty_on_no_rows(monkeypatch):
    monkeypatch.setattr(recovery, "read_sql", lambda sql, **kw: pd.DataFrame())
    assert get_rollback_candidates(FQN) == []


def test_candidates_column_missing_returns_empty(monkeypatch):
    def _raise(sql, **kw):
        raise RuntimeError('no such column: "metadata_location_before"')
    monkeypatch.setattr(recovery, "read_sql", _raise)
    assert get_rollback_candidates(FQN) == []


# ══════════════════════════════════════════════════════════════════════════════
#  validate_rollback_target — live (non-local) mode
# ══════════════════════════════════════════════════════════════════════════════

def test_validate_live_missing_object_refuses_with_72h_message(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(recovery.s3_client, "parse_s3_uri", lambda uri: ("bucket", "key.json"))
    monkeypatch.setattr(recovery.s3_client, "head_object", lambda b, k: None)

    result = validate_rollback_target(FQN, "s3://bucket/key.json")

    assert result.valid is False
    assert result.reason == ORPHAN_REFUSAL_REASON
    assert "ORPHAN_MIN_AGE_HOURS_FLOOR (72h)" in result.reason


def test_validate_live_valid_metadata_reports_snapshot_info(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(recovery.s3_client, "parse_s3_uri", lambda uri: ("bucket", "key.json"))
    monkeypatch.setattr(recovery.s3_client, "head_object", lambda b, k: {"ContentLength": 123})

    metadata = {
        "current-snapshot-id": 42,
        "snapshots": [{"snapshot-id": 41}, {"snapshot-id": 42, "manifest-list": "s3://bucket/ml.avro"}],
    }
    monkeypatch.setattr(recovery.s3_client, "get_object_bytes", lambda b, k: json.dumps(metadata).encode())

    result = validate_rollback_target(FQN, "s3://bucket/key.json")

    assert result.valid is True
    assert result.snapshot_id == 42
    assert result.snapshot_count == 2
    # fastavro isn't installed in this environment -- spot-check degrades gracefully
    assert "spot-check skipped" in result.reason


def test_validate_live_missing_snapshot_fields_invalid(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(recovery.s3_client, "parse_s3_uri", lambda uri: ("bucket", "key.json"))
    monkeypatch.setattr(recovery.s3_client, "head_object", lambda b, k: {"ContentLength": 1})
    monkeypatch.setattr(recovery.s3_client, "get_object_bytes", lambda b, k: b'{"not": "iceberg metadata"}')

    result = validate_rollback_target(FQN, "s3://bucket/key.json")

    assert result.valid is False
    assert "not valid Iceberg metadata" in result.reason


def test_validate_live_parse_failure_invalid(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(recovery.s3_client, "parse_s3_uri", lambda uri: ("bucket", "key.json"))
    monkeypatch.setattr(recovery.s3_client, "head_object", lambda b, k: {"ContentLength": 1})
    monkeypatch.setattr(recovery.s3_client, "get_object_bytes", lambda b, k: b"not json")

    result = validate_rollback_target(FQN, "s3://bucket/key.json")

    assert result.valid is False
    assert "failed to parse" in result.reason


# ══════════════════════════════════════════════════════════════════════════════
#  validate_rollback_target — local mode simulation
# ══════════════════════════════════════════════════════════════════════════════

def test_validate_local_orphaned_marker_refuses(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", True)
    result = validate_rollback_target(FQN, "s3://demo/foo_orphaned.metadata.json")
    assert result.valid is False
    assert result.reason == ORPHAN_REFUSAL_REASON


def test_validate_local_default_is_valid(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", True)
    result = validate_rollback_target(FQN, "s3://demo/normal.metadata.json")
    assert result.valid is True
    assert result.snapshot_id is not None


# ══════════════════════════════════════════════════════════════════════════════
#  rollback_metadata
# ══════════════════════════════════════════════════════════════════════════════

def test_rollback_refuses_on_invalid_target_no_writes(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", True)
    monkeypatch.setattr(recovery, "validate_rollback_target", lambda fqn, loc: recovery.ValidationResult(False, "bad", loc))

    write_calls = []
    audit_calls = []
    monkeypatch.setattr(recovery.execution_log, "write", lambda entry, **kw: write_calls.append(entry))
    monkeypatch.setattr(recovery, "audit", lambda ev: audit_calls.append(ev))

    ok = rollback_metadata(FQN, "s3://demo/bad_orphaned.metadata.json", actor="sujith", reason="test", dry_run=True)

    assert ok is False
    assert write_calls == []
    assert audit_calls == []


def test_rollback_dry_run_makes_no_glue_call(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(
        recovery, "validate_rollback_target",
        lambda fqn, loc: recovery.ValidationResult(True, "ok", loc, snapshot_id=99, snapshot_count=5),
    )
    monkeypatch.setattr(recovery, "_current_metadata_location", lambda fqn: "s3://bucket/current.json")

    glue_calls = []
    monkeypatch.setattr(
        recovery.glue_client, "update_metadata_location",
        lambda db, table, loc, dry_run=False: glue_calls.append((db, table, loc, dry_run)) or True,
    )
    write_calls = []
    monkeypatch.setattr(recovery.execution_log, "write", lambda entry, **kw: write_calls.append(entry))
    monkeypatch.setattr(recovery, "audit", lambda ev: True)
    monkeypatch.setattr(recovery.notifier, "send_alert", lambda **kw: None)

    ok = rollback_metadata(FQN, "s3://bucket/target.json", actor="sujith", reason="test", dry_run=True)

    assert ok is True
    assert glue_calls == [("finance_master_db", "fin_payment_master", "s3://bucket/target.json", True)]
    assert write_calls[0].operation == "ROLLBACK"
    assert write_calls[0].status == "DRY_RUN"
    assert write_calls[0].integrity_status == "SKIPPED"
    assert write_calls[0].metadata_location_before == "s3://bucket/current.json"
    assert write_calls[0].metadata_location_after == "s3://bucket/target.json"


def test_rollback_real_path_preserves_parameters_and_writes_log(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(
        recovery, "validate_rollback_target",
        lambda fqn, loc: recovery.ValidationResult(True, "ok", loc, snapshot_id=7, snapshot_count=3),
    )

    locations = iter(["s3://bucket/current.json", "s3://bucket/target.json"])
    monkeypatch.setattr(recovery, "_current_metadata_location", lambda fqn: next(locations))
    monkeypatch.setattr(recovery.glue_client, "update_metadata_location", lambda db, table, loc, dry_run=False: True)

    write_calls = []
    audit_calls = []
    monkeypatch.setattr(recovery.execution_log, "write", lambda entry, **kw: write_calls.append(entry))
    monkeypatch.setattr(recovery, "audit", lambda ev: audit_calls.append(ev))
    monkeypatch.setattr(recovery.notifier, "send_alert", lambda **kw: None)

    ok = rollback_metadata(FQN, "s3://bucket/target.json", actor="sujith", reason="incident 123", dry_run=False)

    assert ok is True
    entry = write_calls[0]
    assert entry.operation == "ROLLBACK"
    assert entry.status == "SUCCESS"
    assert entry.integrity_status == "VERIFIED"
    assert entry.metadata_location_before == "s3://bucket/current.json"
    assert entry.metadata_location_after == "s3://bucket/target.json"
    assert audit_calls[0].action_type == "metadata_rollback"
    assert audit_calls[0].reason == "incident 123"


def test_rollback_glue_write_failure_returns_false(monkeypatch):
    monkeypatch.setattr(recovery, "ZAMBONI_LOCAL_MODE", False)
    monkeypatch.setattr(
        recovery, "validate_rollback_target",
        lambda fqn, loc: recovery.ValidationResult(True, "ok", loc, snapshot_id=1),
    )
    monkeypatch.setattr(recovery, "_current_metadata_location", lambda fqn: "s3://bucket/current.json")
    monkeypatch.setattr(recovery.glue_client, "update_metadata_location", lambda *a, **kw: False)

    write_calls = []
    monkeypatch.setattr(recovery.execution_log, "write", lambda entry, **kw: write_calls.append(entry))

    ok = rollback_metadata(FQN, "s3://bucket/target.json", actor="sujith", reason="test", dry_run=False)

    assert ok is False
    assert write_calls == []
