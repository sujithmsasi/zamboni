"""
Unit tests for CLI tools — pure logic only, no AWS required.
"""
import pytest
from click.testing import CliRunner


# ── register helpers ──────────────────────────────────────────────────────────

def test_infer_domain_preprod():
    from engine.cli.register import _infer_domain
    assert _infer_domain("finance_preprod") == "finance"


def test_infer_domain_dev():
    from engine.cli.register import _infer_domain
    assert _infer_domain("ers_dev") == "ers"


def test_infer_domain_no_suffix():
    from engine.cli.register import _infer_domain
    assert _infer_domain("finance") == "finance"


def test_infer_layer_staging():
    from engine.cli.register import _infer_layer
    assert _infer_layer("finance_staging_db") == "staging"


def test_infer_layer_datalake():
    from engine.cli.register import _infer_layer
    assert _infer_layer("finance_datalake") == "datalake"


def test_infer_layer_unknown():
    from engine.cli.register import _infer_layer
    result = _infer_layer("finance_db")
    assert result == "staging"  # default


# ── enable CLI ────────────────────────────────────────────────────────────────

def test_enable_requires_table_or_domain():
    from engine.cli.enable import main
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code != 0 or "table" in result.output.lower() or "domain" in result.output.lower()


# ── cost report ───────────────────────────────────────────────────────────────

def test_athena_cost_rate():
    from engine.cli.cost_report import ATHENA_COST_PER_TB
    # Athena is $5/TB in us-west-2
    assert ATHENA_COST_PER_TB == 5.0


def test_cost_calculation():
    from engine.cli.cost_report import ATHENA_COST_PER_TB
    # 1TB scanned = $5.00
    bytes_scanned = 1_000_000_000_000  # 1TB
    cost = bytes_scanned / 1e12 * ATHENA_COST_PER_TB
    assert round(cost, 2) == 5.00


def test_cost_small_scan():
    from engine.cli.cost_report import ATHENA_COST_PER_TB
    # 10GB scanned = $0.05
    bytes_scanned = 10_000_000_000  # 10GB
    cost = bytes_scanned / 1e12 * ATHENA_COST_PER_TB
    assert round(cost, 4) == 0.05


# ── dry_run CLI ───────────────────────────────────────────────────────────────

def test_dry_run_requires_table_or_domain():
    from engine.cli.dry_run import main
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code != 0


# ── fleet_status CLI ──────────────────────────────────────────────────────────

def test_fleet_status_commands_exist():
    from engine.cli.fleet_status import cli
    assert "coverage" in cli.commands
    assert "health"   in cli.commands
    assert "stale"    in cli.commands
