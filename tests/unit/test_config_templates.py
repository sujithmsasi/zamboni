"""
Unit tests for engine/core/config.py — template loading and inference.
No AWS required.
"""
from config.settings import SNAPSHOT_MIN_FLOOR
from engine.core.config import (
    _to_sql_array,
    get_policy_templates,
    get_template,
    infer_template,
)


def test_templates_load():
    templates = get_policy_templates()
    assert isinstance(templates, dict)
    assert len(templates) > 0


def test_expected_templates_exist():
    templates = get_policy_templates()
    expected = [
        "STAGING_DEFAULT",
        "DATALAKE_DEFAULT",
        "BASE_SCD2",
        "MASTER_DEFAULT",
        "CRITICAL_HIGH_VOL",
        "NON_PROD_DEFAULT",
    ]
    for name in expected:
        assert name in templates, f"Missing template: {name}"


def test_all_templates_have_required_fields():
    required = [
        "compaction_strategy",
        "compaction_target_file_size_mb",
        "compaction_engine",
        "snapshot_retention_days",
        "snapshot_min_to_keep",
        "orphan_file_retention_days",
        "run_frequency",
        "window_config",
    ]
    for name, template in get_policy_templates().items():
        for field in required:
            assert field in template, f"Template '{name}' missing field '{field}'"


def test_snapshot_min_to_keep_respects_floor():
    for name, template in get_policy_templates().items():
        assert template["snapshot_min_to_keep"] >= SNAPSHOT_MIN_FLOOR, (
            f"Template '{name}' has snapshot_min_to_keep "
            f"{template['snapshot_min_to_keep']} < floor {SNAPSHOT_MIN_FLOOR}"
        )


def test_get_template_valid():
    t = get_template("STAGING_DEFAULT")
    assert t is not None
    assert t["compaction_strategy"] == "binpack"


def test_get_template_invalid():
    t = get_template("NONEXISTENT_TEMPLATE")
    assert t is None


def test_infer_template_staging_standard():
    assert infer_template("staging", "standard") == "STAGING_DEFAULT"


def test_infer_template_staging_critical():
    assert infer_template("staging", "critical") == "CRITICAL_HIGH_VOL"


def test_infer_template_base():
    assert infer_template("base", "standard") == "BASE_SCD2"


def test_infer_template_master():
    assert infer_template("master", "critical") == "MASTER_DEFAULT"


def test_infer_template_unknown_falls_back():
    result = infer_template("unknown_layer", "unknown_tier")
    assert result == "STAGING_DEFAULT"


# ── _to_sql_array ─────────────────────────────────────────────────────────────

def test_to_sql_array_none():
    assert _to_sql_array(None) == "NULL"


def test_to_sql_array_empty():
    assert _to_sql_array([]) == "NULL"


def test_to_sql_array_single():
    assert _to_sql_array(["effective_date"]) == "ARRAY['effective_date']"


def test_to_sql_array_multiple():
    result = _to_sql_array(["col_a", "col_b"])
    assert result == "ARRAY['col_a', 'col_b']"
