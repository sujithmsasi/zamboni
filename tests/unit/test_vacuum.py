"""
Unit tests for vacuum SQL generation and safety guardrails.
No AWS required.
"""
import pytest
from unittest.mock import patch, MagicMock
from config.settings import SNAPSHOT_MIN_FLOOR, ORPHAN_MIN_RETENTION_HOURS


# ── Snapshot floor enforcement ────────────────────────────────────────────────

def test_snapshot_floor_constant():
    assert SNAPSHOT_MIN_FLOOR >= 30, "Hard floor must be at least 30 snapshots"


def test_orphan_min_retention_constant():
    assert ORPHAN_MIN_RETENTION_HOURS >= 48, "Orphan min retention must be at least 48 hours"


def test_vacuum_skips_when_at_floor():
    """Vacuum should skip if snapshot_count <= min_to_keep."""
    from engine.operations.vacuum import run_expire_snapshots
    from engine.core.health_checker import HealthResult

    health = HealthResult(
        table_fqn="glue_catalog.test_db.test_table",
        snapshot_count=25,   # below floor of 30
    )
    hk_config = {
        "snapshot_retention_days": 7,
        "snapshot_min_to_keep": 30,
    }

    result = run_expire_snapshots(
        table_fqn="glue_catalog.test_db.test_table",
        hk_config=hk_config,
        health=health,
        tier="standard",
        dry_run=True,
    )

    assert result["skipped"] is True
    assert "min_to_keep" in result["skip_reason"]
    assert result["snapshots_expired"] == 0


def test_vacuum_skips_when_exactly_at_floor():
    from engine.operations.vacuum import run_expire_snapshots
    from engine.core.health_checker import HealthResult

    health = HealthResult(
        table_fqn="glue_catalog.test_db.test_table",
        snapshot_count=30,   # exactly at floor
    )
    hk_config = {"snapshot_retention_days": 7, "snapshot_min_to_keep": 30}

    result = run_expire_snapshots(
        "glue_catalog.test_db.test_table", hk_config, health, "standard", dry_run=True
    )
    assert result["skipped"] is True


def test_orphan_retention_respects_minimum():
    """Orphan cleanup should use at least ORPHAN_MIN_RETENTION_HOURS."""
    from engine.operations.vacuum import run_orphan_cleanup

    hk_config = {"orphan_file_retention_days": 1}  # 24h — below minimum of 48h

    with patch("engine.operations.vacuum.run_query") as mock_run:
        mock_run.return_value = "mock-query-id"
        result = run_orphan_cleanup(
            "glue_catalog.test_db.test_table",
            hk_config,
            tier="standard",
            dry_run=True,
        )

    # Even though config says 1 day (24h), should use at least 48h
    assert result["retention_hours"] >= ORPHAN_MIN_RETENTION_HOURS


def test_orphan_retention_longer_config_respected():
    """If configured retention > minimum, use the configured value."""
    from engine.operations.vacuum import run_orphan_cleanup

    hk_config = {"orphan_file_retention_days": 5}  # 120h — above minimum

    with patch("engine.operations.vacuum.run_query") as mock_run:
        mock_run.return_value = "mock-query-id"
        result = run_orphan_cleanup(
            "glue_catalog.test_db.test_table",
            hk_config,
            tier="standard",
            dry_run=True,
        )

    assert result["retention_hours"] == 120  # 5 days * 24h
