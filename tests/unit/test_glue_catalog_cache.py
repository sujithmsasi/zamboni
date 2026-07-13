"""
Glue catalog cache tests -- GLUE_CATALOG_CACHE_TTL_HOURS (config/settings.py).

get_databases()/get_tables() cache their real (non-local-mode) result for
GLUE_CATALOG_CACHE_TTL_HOURS unless force_refresh=True is passed -- added to
stop the interactive Browse & Register UI from hitting AWS Glue's
account-wide API rate limit on every keystroke/tab-refocus. CLI/scheduled
callers (engine.cli.register, LifecycleEngine.run_scan, the synthetic-table
cleanup script) always pass force_refresh=True and are covered by their own
existing test files, not here.
"""
from __future__ import annotations

import pytest

import engine.utils.glue_client as glue_client


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeGlueClient:
    def __init__(self):
        self.paginator_calls: list[str] = []

    def get_paginator(self, op_name):
        self.paginator_calls.append(op_name)
        if op_name == "get_databases":
            return _FakePaginator([{"DatabaseList": [{"Name": "finance_db"}]}])
        return _FakePaginator([{
            "TableList": [{"Name": "fin_payment", "Parameters": {"table_type": "ICEBERG"}}],
        }])


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """Module-level cache state must not leak between tests."""
    monkeypatch.setattr(glue_client, "ZAMBONI_LOCAL_MODE", False)
    glue_client._databases_cache = None
    glue_client._tables_cache = {}
    yield
    glue_client._databases_cache = None
    glue_client._tables_cache = {}


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeGlueClient()
    monkeypatch.setattr(glue_client, "_get_client", lambda: client)
    return client


def test_get_databases_caches_within_ttl(fake_client):
    first = glue_client.get_databases()
    second = glue_client.get_databases()
    assert first == ["finance_db"]
    assert second == ["finance_db"]
    assert fake_client.paginator_calls.count("get_databases") == 1


def test_get_databases_force_refresh_bypasses_cache(fake_client):
    glue_client.get_databases()
    glue_client.get_databases(force_refresh=True)
    assert fake_client.paginator_calls.count("get_databases") == 2


def test_get_databases_refetches_once_ttl_expires(fake_client):
    glue_client.get_databases()
    # Simulate the TTL having elapsed without waiting for it in real time.
    glue_client._databases_cache["fetched_at"] -= (glue_client.GLUE_CATALOG_CACHE_TTL_HOURS * 3600 + 1)
    glue_client.get_databases()
    assert fake_client.paginator_calls.count("get_databases") == 2


def test_get_tables_caches_per_database_independently(fake_client):
    glue_client.get_tables("db_a")
    glue_client.get_tables("db_b")
    glue_client.get_tables("db_a")  # cache hit -- must not add a 3rd call
    assert fake_client.paginator_calls.count("get_tables") == 2


def test_get_tables_force_refresh_bypasses_cache(fake_client):
    glue_client.get_tables("db_a")
    glue_client.get_tables("db_a", force_refresh=True)
    assert fake_client.paginator_calls.count("get_tables") == 2


def test_get_tables_refetches_once_ttl_expires(fake_client):
    glue_client.get_tables("db_a")
    glue_client._tables_cache["db_a"]["fetched_at"] -= (glue_client.GLUE_CATALOG_CACHE_TTL_HOURS * 3600 + 1)
    glue_client.get_tables("db_a")
    assert fake_client.paginator_calls.count("get_tables") == 2
