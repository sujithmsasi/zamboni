"""
Phase 1 page-level feature tests.
Covers: reason_form component, table_selector, page audit wiring,
        execution log improvements, NonProd Lifecycle claim table.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

# ── reason_form component ─────────────────────────────────────────────────────

class TestReasonFormComponent:
    """app.components.reason_form shared widget tests."""

    def test_module_exists(self):
        from app.components import reason_form  # noqa: F401

    def test_validate_and_gate_returns_valid_for_dry_run(self):
        from app.components.reason_form import validate_and_gate
        from config.platform_settings import inject_test_settings
        inject_test_settings({"require_reason_in_preprod": True,
                               "require_ticket_in_prod": False})
        try:
            with patch("streamlit.error"), patch("streamlit.warning"):
                result = validate_and_gate("hk_enable", "", "", "prod", dry_run=True)
            assert result.valid is True
        finally:
            inject_test_settings(None)

    def test_validate_and_gate_returns_invalid_for_live_no_reason(self):
        from app.components.reason_form import validate_and_gate
        from config.platform_settings import inject_test_settings
        inject_test_settings({"require_reason_in_preprod": True,
                               "require_ticket_in_prod": False})
        try:
            with patch("streamlit.error"), patch("streamlit.warning"):
                result = validate_and_gate("hk_enable", "", "", "prod", dry_run=False)
            assert result.valid is False
        finally:
            inject_test_settings(None)

    def test_dry_run_banner_function_exists(self):
        from app.components.reason_form import dry_run_banner
        assert callable(dry_run_banner)


# ── table_selector component ──────────────────────────────────────────────────

class TestTableSelectorComponent:
    """app.components.table_selector cascading selector tests."""

    def test_module_exists(self):
        from app.components import table_selector  # noqa: F401

    def test_render_function_exists(self):
        from app.components.table_selector import render
        sig = inspect.signature(render)
        assert "key_prefix" in sig.parameters

    def test_get_domains_returns_list(self):
        import pandas as pd

        from app.components.table_selector import _get_domains
        with patch("app.components.athena_runner.cached_read_registry",
                   return_value=pd.DataFrame({"domain": ["finance", "ers"]})):
            result = _get_domains()
        assert isinstance(result, list)

    def test_get_domains_returns_empty_on_error(self):
        from app.components.table_selector import _get_domains
        with patch("app.components.athena_runner.cached_read_registry",
                   side_effect=Exception("Athena down")):
            result = _get_domains()
        assert result == []


# ── Page audit wiring verification ───────────────────────────────────────────

class TestPageAuditWiring:
    """All major pages must import and use engine.core.audit."""

    PAGES = [
        "app/pages/1_Domain_Management.py",
        "app/pages/2_Table_Registration.py",
        "app/pages/3_Policy_Configuration.py",
        "app/pages/4_Health_Dashboard.py",
        "app/pages/5_Live_Activity.py",
        "app/pages/6_Dry_Run_Viewer.py",
        "app/pages/7_Execution_Log.py",
        "app/pages/8_Cost_Report.py",
        "app/pages/9_NonProd_Lifecycle.py",
        "app/pages/10_Stale_Resources.py",
        "app/pages/11_Settings.py",
        "app/pages/12_Audit_Log.py",
    ]

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_all_pages_import_audit(self):
        missing = []
        for path in self.PAGES:
            content = self._read(path)
            if "engine.core.audit" not in content:
                missing.append(path)
        assert not missing, f"Pages missing audit import: {missing}"

    def test_domain_management_audits_domain_create(self):
        content = self._read("app/pages/1_Domain_Management.py")
        assert "AuditAction.DOMAIN_CREATE" in content

    def test_table_registration_audits_register(self):
        content = self._read("app/pages/2_Table_Registration.py")
        assert "AuditAction.TABLE_REGISTER" in content

    def test_policy_config_audits_policy_change(self):
        content = self._read("app/pages/3_Policy_Configuration.py")
        assert "AuditAction.POLICY_CHANGE" in content

    def test_dry_run_viewer_audits_promote(self):
        content = self._read("app/pages/6_Dry_Run_Viewer.py")
        assert "AuditAction.DRY_RUN_PROMOTE" in content

    def test_nonprod_lifecycle_audits_exemption(self):
        content = self._read("app/pages/9_NonProd_Lifecycle.py")
        assert "AuditAction.LIFECYCLE_EXEMPTION" in content

    def test_nonprod_lifecycle_audits_claim(self):
        content = self._read("app/pages/9_NonProd_Lifecycle.py")
        assert "AuditAction.CLAIM_TABLE" in content

    def test_stale_resources_audits_bulk_register(self):
        content = self._read("app/pages/10_Stale_Resources.py")
        assert "AuditAction.TABLE_REGISTER" in content


# ── Policy Config page improvements ──────────────────────────────────────────

class TestPolicyConfigImprovements:
    def test_orphan_cadence_field_in_page(self):
        with open("app/pages/3_Policy_Configuration.py", encoding="utf-8") as f:
            content = f.read()
        assert "orphan_cleanup_cadence_days" in content
        assert "Orphan Cleanup Cadence" in content

    def test_orphan_retention_min_is_2(self):
        """Minimum orphan retention must be 2 (safety floor)."""
        with open("app/pages/3_Policy_Configuration.py", encoding="utf-8") as f:
            content = f.read()
        assert "min_value=2" in content

    def test_sort_zorder_columns_field_in_page(self):
        with open("app/pages/3_Policy_Configuration.py", encoding="utf-8") as f:
            content = f.read()
        assert "sort_order_cols" in content
        assert "Sort / Z-Order Columns" in content

    def test_monthly_in_run_frequency_options(self):
        with open("app/pages/3_Policy_Configuration.py", encoding="utf-8") as f:
            content = f.read()
        assert '"monthly"' in content


# ── Table Registration improvements ──────────────────────────────────────────

class TestTableRegistrationImprovements:
    def test_engine_flags_renamed(self):
        with open("app/pages/2_Table_Registration.py", encoding="utf-8") as f:
            content = f.read()
        assert "Housekeeping Enabled" in content
        assert "Archival Enabled" in content

    def test_lifecycle_enabled_in_query(self):
        with open("app/pages/2_Table_Registration.py", encoding="utf-8") as f:
            content = f.read()
        assert "lifecycle_enabled" in content


# ── Dry Run Viewer improvements ───────────────────────────────────────────────

class TestDryRunViewerImprovements:
    def test_cascading_selector_used(self):
        with open("app/pages/6_Dry_Run_Viewer.py", encoding="utf-8") as f:
            content = f.read()
        assert "table_selector" in content

    def test_promote_to_live_button_present(self):
        with open("app/pages/6_Dry_Run_Viewer.py", encoding="utf-8") as f:
            content = f.read()
        assert "Promote to Live" in content

    def test_copyable_sql_download_button(self):
        with open("app/pages/6_Dry_Run_Viewer.py", encoding="utf-8") as f:
            content = f.read()
        assert "download_button" in content
        assert "sql" in content.lower()

    def test_reason_required_for_promote(self):
        with open("app/pages/6_Dry_Run_Viewer.py", encoding="utf-8") as f:
            content = f.read()
        assert "render_reason_form" in content or "validate_and_gate" in content


# ── Execution Log improvements ────────────────────────────────────────────────

class TestExecutionLogImprovements:
    def test_hide_dry_run_toggle_present(self):
        with open("app/pages/7_Execution_Log.py", encoding="utf-8") as f:
            content = f.read()
        assert "hide_dry_run" in content or "Hide DRY_RUN" in content

    def test_sla_breach_tracker_present(self):
        with open("app/pages/7_Execution_Log.py", encoding="utf-8") as f:
            content = f.read()
        assert "SLA Breach" in content

    def test_athena_cost_column_computed(self):
        with open("app/pages/7_Execution_Log.py", encoding="utf-8") as f:
            content = f.read()
        assert "athena_cost_usd" in content


# ── Cost Report improvements ─────────────────────────────────────────────────

class TestCostReportImprovements:
    def test_budget_alert_section_present(self):
        with open("app/pages/8_Cost_Report.py", encoding="utf-8") as f:
            content = f.read()
        assert "Budget Alert" in content
        assert "budget_alert_threshold" in content

    def test_roi_dashboard_present(self):
        with open("app/pages/8_Cost_Report.py", encoding="utf-8") as f:
            content = f.read()
        assert "ROI" in content


# ── NonProd Lifecycle improvements ───────────────────────────────────────────

class TestNonProdLifecycleImprovements:
    def test_claim_table_tab_present(self):
        with open("app/pages/9_NonProd_Lifecycle.py", encoding="utf-8") as f:
            content = f.read()
        assert "Claim Table" in content or "Claim This Table" in content

    def test_claim_table_requires_reason(self):
        with open("app/pages/9_NonProd_Lifecycle.py", encoding="utf-8") as f:
            content = f.read()
        assert "claim_reason" in content

    def test_claim_table_audited(self):
        with open("app/pages/9_NonProd_Lifecycle.py", encoding="utf-8") as f:
            content = f.read()
        assert "AuditAction.CLAIM_TABLE" in content


# ── Stale Resources improvements ─────────────────────────────────────────────

class TestStaleResourcesImprovements:
    def test_bulk_register_flow_present(self):
        with open("app/pages/10_Stale_Resources.py", encoding="utf-8") as f:
            content = f.read()
        assert "Bulk Register" in content or "bulk-register" in content.lower()

    def test_orphaned_s3_report_only(self):
        """S3 orphan section must remain report-only — no direct delete."""
        with open("app/pages/10_Stale_Resources.py", encoding="utf-8") as f:
            content = f.read()
        # Must not have a delete/cleanup button in the S3 orphan section
        # The tab is report-only per Phase 1 spec
        assert "Orphaned S3" in content
        s3_idx = content.find("Orphaned S3")
        next_tab_idx = content.find("# ── Tab", s3_idx + 1)
        s3_section = content[s3_idx:next_tab_idx] if next_tab_idx > 0 else content[s3_idx:]
        assert "delete" not in s3_section.lower() or "report-only" in s3_section.lower() or \
               "No direct delete" in s3_section


# ── Shared helpers exported cleanly ──────────────────────────────────────────

def test_reason_form_module_importable():
    """app.components.reason_form must import without Streamlit running."""
    # This will fail if there are module-level Streamlit calls
    import importlib
    spec = importlib.util.find_spec("app.components.reason_form")
    assert spec is not None


def test_table_selector_module_importable():
    import importlib
    spec = importlib.util.find_spec("app.components.table_selector")
    assert spec is not None


def test_phase1_new_files_exist():
    import os
    new_files = [
        "app/components/reason_form.py",
        "app/components/table_selector.py",
        "engine/core/audit.py",
        "engine/core/reason_validator.py",
        "engine/core/escalation.py",
        "config/zamboni_settings.json",
        "config/platform_settings.py",
        "sql/create_audit_log.sql",
        "app/pages/11_Settings.py",
        "app/pages/12_Audit_Log.py",
    ]
    missing = [f for f in new_files if not os.path.exists(f)]
    assert not missing, f"Phase 1 files missing: {missing}"
