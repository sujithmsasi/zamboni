"""
Sprint 1 gap closure tests.
Covers: dry_run_until ramp-up logic (C2), settings test mode (M4).
"""
import os
import pytest
from datetime import date, timedelta


# ── C2: dry_run_until ramp-up ─────────────────────────────────────────────────

def test_is_in_dry_run_ramp_future_date():
    """Table with dry_run_until in the future IS in ramp-up."""
    from engine.core.registry import is_in_dry_run_ramp
    future = (date.today() + timedelta(days=7)).isoformat()
    assert is_in_dry_run_ramp({"dry_run_until": future}) is True


def test_is_in_dry_run_ramp_today():
    """Table with dry_run_until = today IS in ramp-up (inclusive)."""
    from engine.core.registry import is_in_dry_run_ramp
    today = date.today().isoformat()
    assert is_in_dry_run_ramp({"dry_run_until": today}) is True


def test_is_in_dry_run_ramp_past_date():
    """Table with dry_run_until in the past is NOT in ramp-up."""
    from engine.core.registry import is_in_dry_run_ramp
    past = (date.today() - timedelta(days=1)).isoformat()
    assert is_in_dry_run_ramp({"dry_run_until": past}) is False


def test_is_in_dry_run_ramp_none():
    """Table with no dry_run_until is NOT in ramp-up."""
    from engine.core.registry import is_in_dry_run_ramp
    assert is_in_dry_run_ramp({"dry_run_until": None}) is False


def test_is_in_dry_run_ramp_missing_key():
    """Table row with no dry_run_until key is NOT in ramp-up."""
    from engine.core.registry import is_in_dry_run_ramp
    assert is_in_dry_run_ramp({}) is False


def test_is_in_dry_run_ramp_date_object():
    """Works with date object as well as string."""
    from engine.core.registry import is_in_dry_run_ramp
    future = date.today() + timedelta(days=3)
    assert is_in_dry_run_ramp({"dry_run_until": future}) is True


# ── M4: settings test mode ────────────────────────────────────────────────────

def test_settings_test_mode_active():
    """ZAMBONI_TEST_MODE should be set by conftest."""
    assert os.environ.get("ZAMBONI_TEST_MODE") == "true"


def test_settings_loads_without_real_env():
    """settings.py should load cleanly in test mode without a .env file."""
    from config.settings import (
        AWS_REGION,
        ATHENA_CATALOG,
        ATHENA_DATABASE,
        ATHENA_RESULTS_BUCKET,
        STAGING_BUCKET,
        ARCHIVE_BUCKET,
        ZAMBONI_METADATA_BUCKET,
        SNS_ALERT_TOPIC_ARN,
        SNS_GREENZONE_TOPIC_ARN,
    )
    # All required vars should resolve to mock values
    assert ATHENA_RESULTS_BUCKET.startswith("s3://")
    assert STAGING_BUCKET.startswith("s3://")
    assert ARCHIVE_BUCKET.startswith("s3://")
    assert ZAMBONI_METADATA_BUCKET.startswith("s3://")
    assert SNS_ALERT_TOPIC_ARN.startswith("arn:aws:sns:")
    assert SNS_GREENZONE_TOPIC_ARN.startswith("arn:aws:sns:")


def test_req_uses_mock_in_test_mode():
    """_req() returns mock value when env var not set and _TEST=true."""
    from config.settings import _req
    result = _req("__NONEXISTENT_VAR__", "s3://mock-default/")
    assert result == "s3://mock-default/"


def test_req_prefers_real_env_var():
    """_req() returns real env var value when set, ignoring mock."""
    os.environ["__ZAMBONI_TEST_VAR__"] = "real-value"
    from config.settings import _req
    result = _req("__ZAMBONI_TEST_VAR__", "mock-value")
    assert result == "real-value"
    del os.environ["__ZAMBONI_TEST_VAR__"]


# ── C1: verify systemd entrypoint ─────────────────────────────────────────────

def test_streamlit_entrypoint_exists():
    """app/Home.py (the Streamlit entrypoint) must exist."""
    import os
    assert os.path.exists("app/Home.py"), \
        "app/Home.py not found — Streamlit entrypoint is missing"


def test_systemd_service_uses_correct_entrypoint():
    """deploy/scripts/after_install.sh must reference app/Home.py not app/main.py."""
    with open("deploy/scripts/after_install.sh", encoding='utf-8') as f:
        content = f.read()
    assert "app/Home.py" in content, \
        "after_install.sh still references old entrypoint — should be app/Home.py"
    assert "app/main.py" not in content, \
        "after_install.sh still references app/main.py which doesn't exist"


# ── M2: IAM DeleteTable restriction ──────────────────────────────────────────

def test_iam_delete_table_restricted_to_nonprod():
    """IAM policy must NOT allow glue:DeleteTable on wildcard * resources."""
    import json
    with open("deploy/iam_policy.json", encoding='utf-8') as f:
        content = f.read()
    # Remove comment lines for JSON parsing
    lines = [l for l in content.splitlines() if not l.strip().startswith("//")]
    policy = json.loads("\n".join(lines))

    for stmt in policy["Statement"]:
        if "glue:DeleteTable" in stmt.get("Action", []):
            resources = stmt.get("Resource", [])
            # Should NOT have a wildcard resource that allows all databases
            unrestricted = [
                r for r in resources
                if r.endswith(":database/*") or r.endswith(":table/*")
                and "_preprod" not in r
                and "_dev" not in r
                and "_test" not in r
                and "_uat" not in r
            ]
            assert len(unrestricted) == 0, \
                f"glue:DeleteTable has unrestricted resources: {unrestricted}"
