"""
Phase 1 Enterprise Features -- unit tests.
Covers: audit log, reason validator, escalation matrix, platform settings.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ── Audit Log ─────────────────────────────────────────────────────────────────

class TestAuditEvent:
    def test_to_dict_has_all_required_fields(self):
        from engine.core.audit import AuditAction, AuditEvent
        ev = AuditEvent(
            actor="sujith", action_type=AuditAction.HK_ENABLE,
            page_source="3_Policy_Configuration",
            target_type="table", target_id="glue_catalog.fin.t1",
            domain="finance", environment="prod",
            dry_run=False, status="SUCCESS",
            reason="Monthly enablement batch",
        )
        d = ev.to_dict()
        required = ["audit_id", "timestamp", "actor", "action_type",
                    "page_source", "target_type", "target_id",
                    "domain", "environment", "dry_run", "status",
                    "reason", "ticket_number", "before_value",
                    "after_value", "error_message", "audit_date"]
        for field in required:
            assert field in d, f"Missing field: {field}"

    def test_auto_generates_audit_id(self):
        from engine.core.audit import AuditEvent
        e1 = AuditEvent(actor="a", action_type="x", page_source="p",
                        target_type="t", target_id="i")
        e2 = AuditEvent(actor="a", action_type="x", page_source="p",
                        target_type="t", target_id="i")
        assert e1.audit_id != e2.audit_id  # each event gets unique ID

    def test_failure_clone_sets_status(self):
        from engine.core.audit import AuditEvent
        base = AuditEvent(actor="a", action_type="hk_enable",
                          page_source="p", target_type="table", target_id="t")
        fail = AuditEvent.failure(base, "Athena timeout")
        assert fail.status == "FAILURE"
        assert fail.error_message == "Athena timeout"
        assert fail.audit_id != base.audit_id

    def test_rejected_clone_sets_status(self):
        from engine.core.audit import AuditEvent
        base = AuditEvent(actor="a", action_type="run_hk_now",
                          page_source="p", target_type="table", target_id="t")
        rej = AuditEvent.rejected(base, "Missing ticket number")
        assert rej.status == "REJECTED"

    def test_audit_never_raises_on_persist_error(self):
        """audit() must return False but never raise if Athena is down."""
        from engine.core.audit import AuditEvent, audit
        ev = AuditEvent(actor="a", action_type="hk_enable",
                        page_source="p", target_type="t", target_id="i")
        with patch("engine.core.audit._persist", side_effect=Exception("Athena down")):
            result = audit(ev)
        assert result is False  # graceful failure, no raise

    def test_audit_returns_true_on_success(self):
        from engine.core.audit import AuditEvent, audit
        ev = AuditEvent(actor="a", action_type="hk_enable",
                        page_source="p", target_type="t", target_id="i")
        with patch("engine.core.audit._persist", return_value=None):
            result = audit(ev)
        assert result is True

    def test_audit_action_convenience_wrapper(self):
        from engine.core.audit import AuditAction, audit_action
        with patch("engine.core.audit._persist") as mp:
            result = audit_action(
                actor="a", action_type=AuditAction.HK_ENABLE,
                page_source="p", target_type="table", target_id="t",
                status="SUCCESS",
            )
        assert result is True
        assert mp.called

    def test_diff_json_returns_two_strings(self):
        from engine.core.audit import diff_json
        before = {"hk_enabled": False, "tier": "standard"}
        after  = {"hk_enabled": True,  "tier": "standard"}
        bv, av = diff_json(before, after)
        assert isinstance(bv, str)
        assert isinstance(av, str)
        assert json.loads(bv)["hk_enabled"] is False
        assert json.loads(av)["hk_enabled"] is True

    def test_all_audit_action_constants_are_strings(self):
        import inspect

        from engine.core.audit import AuditAction
        for name, val in inspect.getmembers(AuditAction):
            if not name.startswith("_"):
                assert isinstance(val, str), f"AuditAction.{name} must be str"

    def test_audit_event_dry_run_recorded(self):
        """DRY_RUN status is preserved in the dict."""
        from engine.core.audit import AuditEvent
        ev = AuditEvent(actor="a", action_type="x", page_source="p",
                        target_type="t", target_id="i",
                        dry_run=True, status="DRY_RUN")
        d = ev.to_dict()
        assert d["dry_run"] is True
        assert d["status"] == "DRY_RUN"


# ── Reason Validator ──────────────────────────────────────────────────────────

class TestReasonValidator:
    def _validate(self, action, reason="", ticket="",
                  env="prod", dry_run=False):
        from config.platform_settings import inject_test_settings
        from engine.core.reason_validator import validate
        inject_test_settings({
            "require_reason_in_preprod": True,
            "require_reason_in_dev":     False,
            "require_ticket_in_prod":    False,
        })
        try:
            return validate(action, reason, ticket, env, dry_run)
        finally:
            inject_test_settings(None)

    def test_dry_run_always_valid(self):
        result = self._validate("hk_enable", reason="", dry_run=True)
        assert result.valid is True

    def test_prod_live_action_needs_reason(self):
        result = self._validate("hk_enable", reason="", env="prod", dry_run=False)
        assert result.valid is False
        assert any("reason" in e.lower() for e in result.errors)

    def test_prod_live_action_with_reason_is_valid(self):
        result = self._validate("hk_enable", reason="Monthly enablement batch",
                                env="prod", dry_run=False)
        assert result.valid is True

    def test_reason_too_short_fails(self):
        result = self._validate("hk_enable", reason="ok", env="prod", dry_run=False)
        assert result.valid is False
        assert any("short" in e.lower() for e in result.errors)

    def test_dev_env_no_reason_required_by_default(self):
        result = self._validate("hk_enable", reason="", env="dev", dry_run=False)
        assert result.valid is True

    def test_ticket_required_in_prod_when_configured(self):
        from config.platform_settings import inject_test_settings
        from engine.core.reason_validator import validate
        inject_test_settings({
            "require_reason_in_preprod": True,
            "require_reason_in_dev":     False,
            "require_ticket_in_prod":    True,
        })
        try:
            result = validate(
                "dry_run_promote",
                reason="Testing after approval",
                ticket_number="",
                environment="prod",
                dry_run=False,
            )
            assert result.valid is False
            assert any("ticket" in e.lower() for e in result.errors)
        finally:
            inject_test_settings(None)

    def test_ticket_not_required_when_setting_off(self):
        result = self._validate(
            "dry_run_promote",
            reason="Promoting to live after 2-week dry run",
            ticket="",
            env="prod", dry_run=False,
        )
        assert result.valid is True

    def test_require_reason_for_action_returns_bool(self):
        from config.platform_settings import inject_test_settings
        from engine.core.reason_validator import require_reason_for_action
        inject_test_settings({"require_reason_in_preprod": True,
                               "require_reason_in_dev": False})
        try:
            assert require_reason_for_action("hk_enable", "prod") is True
            assert require_reason_for_action("hk_enable", "dev")  is False
        finally:
            inject_test_settings(None)

    def test_non_destructive_action_no_reason_needed(self):
        """domain_create does not require a reason."""
        result = self._validate("domain_create", reason="", env="prod", dry_run=False)
        assert result.valid is True

    def test_dry_run_warning_when_no_reason(self):
        result = self._validate("hk_enable", reason="", dry_run=True)
        assert result.valid is True
        assert len(result.warnings) > 0


# ── Escalation Matrix ─────────────────────────────────────────────────────────

class TestEscalation:
    def _with_matrix(self, matrix: dict):
        from config.platform_settings import inject_test_settings
        inject_test_settings({"escalation_matrix": matrix})

    def teardown_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def test_most_specific_match_wins(self):
        from engine.core.escalation import lookup
        self._with_matrix({
            "domain:finance|tier:critical|env:prod": {
                "primary_owner_email": "finance-critical@company.com"},
            "domain:finance": {
                "primary_owner_email": "finance@company.com"},
            "default": {
                "primary_owner_email": "default@company.com"},
        })
        result = lookup("finance", "critical", "prod")
        assert result["primary_owner_email"] == "finance-critical@company.com"

    def test_falls_back_to_domain_only(self):
        from engine.core.escalation import lookup
        self._with_matrix({
            "domain:finance": {"primary_owner_email": "finance@company.com"},
            "default":        {"primary_owner_email": "default@company.com"},
        })
        result = lookup("finance", "standard", "prod")
        assert result["primary_owner_email"] == "finance@company.com"

    def test_falls_back_to_default(self):
        from engine.core.escalation import lookup
        self._with_matrix({
            "default": {"primary_owner_email": "default@company.com"},
        })
        result = lookup("unknown_domain", "low", "dev")
        assert result["primary_owner_email"] == "default@company.com"

    def test_empty_matrix_returns_default_entry(self):
        from engine.core.escalation import lookup
        self._with_matrix({})
        result = lookup("finance", "critical", "prod")
        assert isinstance(result, dict)
        assert "severity_rules" in result

    def test_get_severity_uses_rules(self):
        from engine.core.escalation import get_severity
        self._with_matrix({
            "default": {
                "severity_rules": {"FAILURE_TIMEOUT": "high"}
            }
        })
        sev = get_severity("FAILURE_TIMEOUT")
        assert sev == "high"

    def test_get_severity_tier_default(self):
        from engine.core.escalation import get_severity
        self._with_matrix({})
        assert get_severity("UNKNOWN_REASON", tier="critical") == "high"
        assert get_severity("UNKNOWN_REASON", tier="low")      == "low"

    def test_list_matrix_returns_list(self):
        from engine.core.escalation import list_matrix
        self._with_matrix({"default": {"primary_owner_email": "x@y.com"}})
        entries = list_matrix()
        assert isinstance(entries, list)
        assert any(e.get("_key") == "default" for e in entries)

    def test_tier_env_fallback(self):
        from engine.core.escalation import lookup
        self._with_matrix({
            "tier:critical|env:prod": {"escalation_email": "ops@company.com"},
            "default":               {"escalation_email": ""},
        })
        result = lookup(None, "critical", "prod")
        assert result["escalation_email"] == "ops@company.com"


# ── Platform Settings ─────────────────────────────────────────────────────────

class TestPlatformSettings:
    def setup_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def teardown_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def test_get_settings_returns_dict(self):
        from config.platform_settings import get_settings
        result = get_settings()
        assert isinstance(result, dict)

    def test_get_settings_returns_safe_defaults_in_test_mode(self):
        from config.platform_settings import get_settings
        result = get_settings()
        assert "execution_log_retention_days" in result
        assert "default_dry_run" in result
        assert "escalation_matrix" in result

    def test_inject_test_settings_overrides(self):
        from config.platform_settings import get_settings, inject_test_settings
        inject_test_settings({"my_key": "my_value"})
        result = get_settings()
        assert result.get("my_key") == "my_value"

    def test_inject_none_resets(self):
        from config.platform_settings import get_settings, inject_test_settings
        inject_test_settings({"x": 1})
        inject_test_settings(None)
        result = get_settings()
        assert "x" not in result

    def test_get_setting_returns_value(self):
        from config.platform_settings import get_setting, inject_test_settings
        inject_test_settings({"some_key": 42})
        assert get_setting("some_key") == 42

    def test_get_setting_returns_default(self):
        from config.platform_settings import get_setting, inject_test_settings
        inject_test_settings({})
        assert get_setting("nonexistent", "fallback") == "fallback"

    def test_set_setting_dry_run_does_not_save(self):
        from config.platform_settings import inject_test_settings, set_setting
        inject_test_settings({"x": 1})
        # dry_run=True should not call save_settings
        with patch("config.platform_settings.save_settings") as ms:
            set_setting("x", 99, actor="sujith", dry_run=True)
        ms.assert_not_called()

    def test_save_settings_in_test_mode_updates_override(self):
        from config.platform_settings import get_settings, inject_test_settings, save_settings
        inject_test_settings({"a": 1})
        save_settings({"a": 2, "b": 3})
        result = get_settings()
        assert result["a"] == 2
        assert result["b"] == 3

    def test_safe_defaults_has_required_keys(self):
        from config.platform_settings import _safe_defaults
        d = _safe_defaults()
        required = [
            "execution_log_retention_days",
            "audit_log_retention_days",
            "default_dry_run",
            "require_reason_in_preprod",
            "require_ticket_in_prod",
            "escalation_matrix",
        ]
        for k in required:
            assert k in d, f"_safe_defaults missing: {k}"


# ── Audit Log DDL ─────────────────────────────────────────────────────────────

def test_audit_log_sql_exists():
    import os
    assert os.path.exists("sql/create_audit_log.sql"), \
        "sql/create_audit_log.sql must exist"


def test_audit_log_sql_has_required_columns():
    with open("sql/create_audit_log.sql", encoding="utf-8") as f:
        ddl = f.read()
    required_cols = [
        "audit_id", "timestamp", "actor", "action_type",
        "page_source", "target_type", "target_id", "domain",
        "environment", "dry_run", "status", "reason",
        "ticket_number", "before_value", "after_value",
        "error_message", "audit_date",
    ]
    for col in required_cols:
        assert col in ddl, f"DDL missing column: {col}"


def test_audit_log_table_in_settings():
    from config.settings import AUDIT_LOG_TABLE
    assert "audit_log" in AUDIT_LOG_TABLE


# ── Settings page exists ──────────────────────────────────────────────────────

def test_settings_page_exists():
    import os
    assert os.path.exists("app/pages/11_Settings.py")


def test_audit_log_page_exists():
    import os
    assert os.path.exists("app/pages/12_Audit_Log.py")


def test_pages_toml_has_settings():
    with open(".streamlit/pages.toml", encoding="utf-8") as f:
        content = f.read()
    assert "11_Settings.py" in content
    assert "12_Audit_Log.py" in content


def test_zamboni_settings_json_exists():
    import os
    assert os.path.exists("config/zamboni_settings.json")


def test_zamboni_settings_json_valid():
    import json
    with open("config/zamboni_settings.json", encoding="utf-8") as f:
        data = json.load(f)
    assert "execution_log_retention_days" in data
    assert "escalation_matrix" in data
