"""
domain_registry.is_active previously had no effect on engine processing --
get_enabled_tables/get_archivable_tables and the lifecycle engine's
table-selection queries never checked it, so "disabling" a domain in
Domain Management only hid it from active-domain pickers. These tests
guard the fix: every table-selection query that feeds HK/Archival/
Lifecycle processing must include the domain-active filter.
"""
from unittest.mock import patch

import pandas as pd

from engine.core import registry


def test_domain_active_filter_sql_shape():
    clause = registry.domain_active_filter_sql()
    assert "domain_registry" in clause
    assert "is_active = false" in clause
    assert "domain IS NULL OR" in clause


def test_domain_active_filter_sql_custom_column():
    clause = registry.domain_active_filter_sql("r.domain")
    assert "r.domain IS NULL OR r.domain NOT IN" in clause


def test_get_enabled_tables_excludes_inactive_domains():
    with patch("engine.core.registry.read_sql") as msql:
        msql.return_value = pd.DataFrame()
        registry.get_enabled_tables(environment="prod")
    sql = msql.call_args[0][0]
    assert "domain_registry" in sql
    assert "is_active = false" in sql


def test_get_archivable_tables_excludes_inactive_domains():
    with patch("engine.core.registry.read_sql") as msql:
        msql.return_value = pd.DataFrame()
        registry.get_archivable_tables()
    sql = msql.call_args[0][0]
    assert "domain_registry" in sql
    assert "is_active = false" in sql


def test_lifecycle_active_registry_tables_excludes_inactive_domains():
    """2026-07-09: tightened from the shared blocklist filter to the
    Lifecycle-only allowlist (registry.domain_registered_active_filter_sql())
    -- an unregistered domain must not be evaluated toward a drop either,
    not just an explicitly deactivated one."""
    from engine.engines.lifecycle_engine import LifecycleEngine
    engine = LifecycleEngine(dry_run=True)
    with patch("engine.engines.lifecycle_engine.read_sql") as msql:
        msql.return_value = pd.DataFrame()
        engine._get_active_registry_tables(environment="preprod")
    sql = msql.call_args[0][0]
    assert "domain_registry" in sql
    assert "is_active = true" in sql
    assert "domain IN (SELECT domain_name" in sql


def test_lifecycle_pending_drop_tables_excludes_inactive_domains():
    """2026-07-09: same tightening as above, applied to the cleanup
    candidate fetch."""
    from engine.engines.lifecycle_engine import LifecycleEngine
    engine = LifecycleEngine(dry_run=True)
    with patch("engine.engines.lifecycle_engine.read_sql") as msql:
        msql.return_value = pd.DataFrame()
        engine._get_pending_drop_tables(environment="preprod")
    sql = msql.call_args[0][0]
    assert "domain_registry" in sql
    assert "is_active = true" in sql
    assert "domain IN (SELECT domain_name" in sql
