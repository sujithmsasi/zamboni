"""
Zamboni — pytest conftest
Sets ZAMBONI_TEST_MODE=true before any test runs so config/settings.py
uses safe mock defaults for required env vars.
No real .env file or AWS credentials needed for unit tests.
"""
import os
import pytest


def pytest_configure(config):
    """Called before test collection. Set test mode env var early."""
    os.environ.setdefault("ZAMBONI_TEST_MODE", "true")
