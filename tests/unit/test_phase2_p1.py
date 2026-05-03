"""
Phase 2.1 tests: Teams webhook, weekly digest, Cost Explorer toggle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ── Teams Notifier ────────────────────────────────────────────────────────────

class TestTeamsNotifier:
    def setup_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def teardown_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def test_is_enabled_false_by_default(self):
        from config.platform_settings import inject_test_settings
        from engine.core.teams_notifier import is_enabled
        inject_test_settings({"teams_enabled": False, "teams_webhook_url": ""})
        assert is_enabled() is False

    def test_is_enabled_true_when_configured(self):
        from config.platform_settings import inject_test_settings
        from engine.core.teams_notifier import is_enabled
        inject_test_settings({
            "teams_enabled":     True,
            "teams_webhook_url": "https://outlook.office.com/webhook/fake",
        })
        assert is_enabled() is True

    def test_is_enabled_false_when_url_missing(self):
        from config.platform_settings import inject_test_settings
        from engine.core.teams_notifier import is_enabled
        inject_test_settings({"teams_enabled": True, "teams_webhook_url": ""})
        assert is_enabled() is False

    def test_should_notify_skips_dry_run(self):
        from engine.core.teams_notifier import should_notify
        assert should_notify("hk_enable", dry_run=True,  status="SUCCESS") is False

    def test_should_notify_skips_dry_run_status(self):
        from engine.core.teams_notifier import should_notify
        assert should_notify("hk_enable", dry_run=False, status="DRY_RUN") is False

    def test_should_notify_true_for_live_success(self):
        from engine.core.teams_notifier import should_notify
        assert should_notify("hk_enable", dry_run=False, status="SUCCESS") is True

    def test_should_notify_false_for_unknown_action(self):
        from engine.core.teams_notifier import should_notify
        assert should_notify("domain_list_view", dry_run=False, status="SUCCESS") is False

    def test_notify_teams_returns_false_when_disabled(self):
        from config.platform_settings import inject_test_settings
        from engine.core.teams_notifier import TeamsEvent, notify_teams
        inject_test_settings({"teams_enabled": False, "teams_webhook_url": ""})
        ev = TeamsEvent(
            title="Test", summary="test", actor="a",
            action_type="hk_enable", target="t",
        )
        assert notify_teams(ev) is False

    def test_notify_teams_never_raises(self):
        """notify_teams must never propagate exceptions."""
        from config.platform_settings import inject_test_settings
        from engine.core.teams_notifier import TeamsEvent, notify_teams
        inject_test_settings({
            "teams_enabled":     True,
            "teams_webhook_url": "https://fake.webhook.url/fail",
        })
        ev = TeamsEvent(
            title="Test", summary="s", actor="a",
            action_type="hk_enable", target="t",
        )
        with patch("engine.core.teams_notifier._http_post",
                   side_effect=Exception("network error")):
            result = notify_teams(ev)
        assert result is False  # no raise, returns False

    def test_build_payload_has_required_keys(self):
        from engine.core.teams_notifier import TeamsEvent, _build_payload
        ev = TeamsEvent(
            title="HK Enabled", summary="finance enabled",
            actor="sujith", action_type="hk_enable",
            target="glue_catalog.fin.t1",
            domain="finance", environment="prod",
            status="SUCCESS", reason="Monthly batch",
        )
        payload = _build_payload(ev)
        assert payload["@type"] == "MessageCard"
        assert "sections" in payload
        assert payload["themeColor"] == "00b894"  # green for SUCCESS

    def test_build_payload_red_for_failure(self):
        from engine.core.teams_notifier import TeamsEvent, _build_payload
        ev = TeamsEvent(
            title="X", summary="x", actor="a",
            action_type="hk_enable", target="t",
            status="FAILURE",
        )
        payload = _build_payload(ev)
        assert payload["themeColor"] == "d63031"

    def test_mask_webhook_url(self):
        from engine.core.teams_notifier import mask_webhook_url
        url    = "https://outlook.office.com/webhook/abc123xyz456"
        masked = mask_webhook_url(url)
        assert masked.endswith("abc123xyz456")
        assert masked.startswith("•")
        assert "office.com" not in masked

    def test_mask_webhook_url_empty(self):
        from engine.core.teams_notifier import mask_webhook_url
        assert mask_webhook_url("") == ""

    def test_audit_fires_teams_notification_on_success(self):
        """audit() must call _maybe_notify_teams after persisting."""
        from engine.core.audit import AuditAction, AuditEvent, audit
        ev = AuditEvent(
            actor="a", action_type=AuditAction.HK_ENABLE,
            page_source="p", target_type="table", target_id="t",
            dry_run=False, status="SUCCESS",
        )
        with patch("engine.core.audit._persist"), \
             patch("engine.core.audit._maybe_notify_teams") as mt:
            audit(ev)
        mt.assert_called_once_with(ev)

    def test_audit_teams_failure_does_not_block(self):
        """Teams failure inside audit() must not prevent audit from succeeding."""
        from engine.core.audit import AuditAction, AuditEvent, audit
        ev = AuditEvent(
            actor="a", action_type=AuditAction.HK_ENABLE,
            page_source="p", target_type="table", target_id="t",
        )
        with patch("engine.core.audit._persist"), \
             patch("engine.core.audit._maybe_notify_teams",
                   side_effect=Exception("teams down")):
            result = audit(ev)
        assert result is True  # audit succeeded despite teams failure


# ── Digest Builder ────────────────────────────────────────────────────────────

class TestDigestBuilder:
    def test_build_digest_returns_error_dict_on_athena_failure(self):
        from engine.core.digest import build_digest
        with patch("engine.core.digest.read_sql",
                   side_effect=Exception("Athena unavailable")):
            result = build_digest("finance", days=7)
        assert "error" in result
        assert result["domain"] == "finance"

    def test_build_digest_has_required_keys_on_success(self):
        import pandas as pd

        from engine.core.digest import build_digest

        # Mock all 4 read_sql calls in _build() with appropriate DataFrames
        empty = pd.DataFrame()
        summary_df = pd.DataFrame([{
            "tables_touched": 10, "successes": 8, "failures": 1,
            "skips": 1, "gb_compacted": 2.5,
            "snapshots_expired": 50, "orphan_files_deleted": 0,
            "athena_cost_usd": 0.0025,
        }])
        domain_df = pd.DataFrame([{
            "owner_email": "da@company.com", "digest_email": "",
        }])

        call_count = 0
        def mock_read(sql, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return summary_df  # summary
            if call_count == 2:
                return empty        # ops
            if call_count == 3:
                return empty        # SLA
            if call_count == 4:
                return empty        # failures
            if call_count == 5:
                return domain_df    # domain info
            return empty            # any extra calls

        with patch("engine.core.digest.read_sql", side_effect=mock_read):
            result = build_digest("finance", days=7)

        required = ["domain", "period_start", "period_end",
                    "summary", "sla_breaches", "top_failures",
                    "recipient_email", "generated"]
        for k in required:
            assert k in result, f"Missing key: {k}"

    def test_digest_module_exists(self):
        from engine.core import digest  # noqa: F401

    def test_get_digest_domains_returns_list(self):
        import pandas as pd

        from engine.core.digest import get_digest_domains
        with patch("engine.core.digest.read_sql",
                   return_value=pd.DataFrame({
                       "domain_name":    ["finance"],
                       "owner_email":    ["da@company.com"],
                       "digest_email":   [""],
                       "digest_enabled": [True],
                   })):
            result = get_digest_domains()
        assert isinstance(result, list)
        assert result[0]["domain_name"] == "finance"

    def test_get_digest_domains_returns_empty_on_error(self):
        from engine.core.digest import get_digest_domains
        with patch("engine.core.digest.read_sql",
                   side_effect=Exception("Athena down")):
            result = get_digest_domains()
        assert result == []


# ── Cost Explorer ─────────────────────────────────────────────────────────────

class TestCostExplorer:
    def setup_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def teardown_method(self):
        from config.platform_settings import inject_test_settings
        inject_test_settings(None)

    def test_is_enabled_false_by_default(self):
        from config.platform_settings import inject_test_settings
        from engine.core.cost_explorer import is_enabled
        inject_test_settings({"cost_explorer_enabled": False})
        assert is_enabled() is False

    def test_is_enabled_true_when_configured(self):
        from config.platform_settings import inject_test_settings
        from engine.core.cost_explorer import is_enabled
        inject_test_settings({"cost_explorer_enabled": True})
        assert is_enabled() is True

    def test_get_athena_cost_returns_estimate_when_disabled(self):
        from config.platform_settings import inject_test_settings
        from engine.core.cost_explorer import get_athena_cost
        inject_test_settings({"cost_explorer_enabled": False})
        result = get_athena_cost(days=7)
        assert result["source"] == "estimate"
        assert result["total_usd"] is None

    def test_get_athena_cost_falls_back_on_ce_error(self):
        from config.platform_settings import inject_test_settings
        from engine.core.cost_explorer import get_athena_cost
        inject_test_settings({"cost_explorer_enabled": True})
        with patch("engine.core.cost_explorer._read_cache", return_value=None), \
             patch("engine.core.cost_explorer._fetch_from_ce",
                   side_effect=Exception("NoCredentialsError")):
            result = get_athena_cost(days=7)
        assert result["source"] == "estimate"
        assert "error" in result["warning"].lower()

    def test_estimate_fallback_has_required_keys(self):
        from engine.core.cost_explorer import _estimate_fallback
        result = _estimate_fallback(30, "test reason")
        required = ["source", "total_usd", "daily", "tag_filtered",
                    "warning", "period_start", "period_end", "fetched_at"]
        for k in required:
            assert k in result, f"Missing key: {k}"
        assert result["source"] == "estimate"

    def test_cache_miss_returns_none(self):
        from engine.core.cost_explorer import _read_cache
        with patch("boto3.client", side_effect=Exception("no creds")):
            result = _read_cache(30)
        assert result is None

    def test_get_cost_summary_for_settings_disabled(self):
        from config.platform_settings import inject_test_settings
        from engine.core.cost_explorer import get_cost_summary_for_settings
        inject_test_settings({"cost_explorer_enabled": False})
        result = get_cost_summary_for_settings()
        assert result["source"] == "disabled"
        assert result["total_usd"] is None


# ── Settings page has new sections ───────────────────────────────────────────

class TestSettingsPageContent:
    def test_teams_section_in_settings(self):
        with open("app/pages/11_Settings.py", encoding="utf-8") as f:
            content = f.read()
        assert "Teams" in content
        assert "teams_enabled" in content
        assert "teams_webhook_url" in content

    def test_cost_explorer_section_in_settings(self):
        with open("app/pages/11_Settings.py", encoding="utf-8") as f:
            content = f.read()
        assert "Cost Explorer" in content
        assert "cost_explorer_enabled" in content

    def test_teams_webhook_masked_in_ui(self):
        with open("app/pages/11_Settings.py", encoding="utf-8") as f:
            content = f.read()
        assert "mask_webhook_url" in content

    def test_test_message_button_present(self):
        with open("app/pages/11_Settings.py", encoding="utf-8") as f:
            content = f.read()
        assert "Send Test Message" in content

    def test_cost_explorer_iam_note_present(self):
        with open("app/pages/11_Settings.py", encoding="utf-8") as f:
            content = f.read()
        assert "ce:GetCostAndUsage" in content


# ── Domain Management digest opt-in ──────────────────────────────────────────

class TestDomainDigestOptIn:
    def test_digest_fields_in_register_form(self):
        with open("app/pages/1_Domain_Management.py", encoding="utf-8") as f:
            content = f.read()
        assert "digest_enabled" in content
        assert "digest_email" in content

    def test_digest_preview_button_present(self):
        with open("app/pages/1_Domain_Management.py", encoding="utf-8") as f:
            content = f.read()
        assert "Preview Digest" in content

    def test_digest_sender_status_shown(self):
        with open("app/pages/1_Domain_Management.py", encoding="utf-8") as f:
            content = f.read()
        assert "email_sender_status" in content


# ── DDL and IAM ───────────────────────────────────────────────────────────────

def test_domain_registry_ddl_has_digest_fields():
    with open("sql/create_domain_registry.sql", encoding="utf-8") as f:
        ddl = f.read()
    assert "digest_enabled" in ddl
    assert "digest_email"   in ddl


def test_iam_policy_has_cost_explorer_permission():
    import json
    with open("deploy/iam_policy.json") as f:
        policy = json.load(f)
    all_actions = []
    for stmt in policy.get("Statement", []):
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        all_actions.extend(actions)
    assert "ce:GetCostAndUsage" in all_actions


def test_zamboni_settings_json_has_teams_config():
    import json
    with open("config/zamboni_settings.json") as f:
        settings = json.load(f)
    assert "teams_enabled"     in settings
    assert "teams_webhook_url" in settings


def test_zamboni_settings_json_has_cost_explorer_config():
    import json
    with open("config/zamboni_settings.json") as f:
        settings = json.load(f)
    assert "cost_explorer_enabled" in settings
    assert "cost_explorer_tag_key" in settings


def test_cost_report_page_uses_cost_explorer():
    with open("app/pages/8_Cost_Report.py", encoding="utf-8") as f:
        content = f.read()
    assert "cost_explorer" in content
    assert "ce_enabled" in content
