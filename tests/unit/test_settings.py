"""
Unit tests for config/settings.py
Validates constants and structure — no AWS required.
"""
import pytest
from unittest.mock import patch
import os


def test_valid_layers():
    from config.settings import VALID_LAYERS
    assert "staging"  in VALID_LAYERS
    assert "datalake" in VALID_LAYERS
    assert "base"     in VALID_LAYERS
    assert "master"   in VALID_LAYERS
    assert len(VALID_LAYERS) == 4


def test_valid_tiers():
    from config.settings import VALID_TIERS
    assert "critical" in VALID_TIERS
    assert "standard" in VALID_TIERS
    assert "low"      in VALID_TIERS


def test_valid_environments():
    from config.settings import VALID_ENVIRONMENTS, NONPROD_ENVIRONMENTS
    assert "prod" in VALID_ENVIRONMENTS
    assert "prod" not in NONPROD_ENVIRONMENTS
    assert "dev"  in NONPROD_ENVIRONMENTS


def test_snapshot_min_floor():
    from config.settings import SNAPSHOT_MIN_FLOOR
    # Hard floor must be at least 30 — never change below this
    assert SNAPSHOT_MIN_FLOOR >= 30


def test_orphan_min_retention():
    from config.settings import ORPHAN_MIN_RETENTION_HOURS
    # Hard minimum 48 hours — never delete files newer than this
    assert ORPHAN_MIN_RETENTION_HOURS >= 48


def test_workgroups_have_all_tiers():
    from config.settings import ATHENA_WORKGROUPS
    required = {"critical", "standard", "low", "archival", "app"}
    assert required.issubset(set(ATHENA_WORKGROUPS.keys()))


def test_default_region():
    from config.settings import AWS_REGION
    assert AWS_REGION == "us-west-2"


def test_default_catalog():
    from config.settings import ATHENA_CATALOG
    assert ATHENA_CATALOG == "glue_catalog"


def test_default_database():
    from config.settings import ATHENA_DATABASE
    assert ATHENA_DATABASE == "zamboni_catalog"


def test_circuit_breaker_threshold():
    from config.settings import CIRCUIT_BREAKER_THRESHOLD
    assert CIRCUIT_BREAKER_THRESHOLD >= 1
