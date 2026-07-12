"""
Zamboni -- pytest conftest
1. Sets ZAMBONI_TEST_MODE=true so settings.py uses safe mock defaults.
2. Sets PYTHONUTF8=1 to prevent cp1252 errors on Windows.
"""
import os
import sys


def pytest_configure(config):
    """Called before test collection."""
    os.environ.setdefault("ZAMBONI_TEST_MODE", "true")
    os.environ.setdefault("PYTHONUTF8", "1")

    if hasattr(sys, "flags") and not sys.flags.utf8_mode:
        try:
            import _bootlocale  # noqa: F401
        except ImportError:
            pass
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
