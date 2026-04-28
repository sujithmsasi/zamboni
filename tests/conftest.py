"""
Zamboni — pytest conftest
1. Sets ZAMBONI_TEST_MODE=true before any test so config/settings.py
   uses safe mock defaults — no real .env or AWS credentials needed.
2. Sets PYTHONUTF8=1 and overrides the default locale encoding to UTF-8
   so open() calls without an explicit encoding= work correctly on Windows
   (which otherwise defaults to cp1252).
"""
import os
import sys


def pytest_configure(config):
    """Called before test collection."""
    # Test mode — use mock env var defaults
    os.environ.setdefault("ZAMBONI_TEST_MODE", "true")

    # Force UTF-8 as the default encoding on Windows.
    # This affects open() calls that omit encoding= — prevents cp1252 errors
    # when reading source files that contain UTF-8 em-dashes, checkmarks etc.
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys, "flags") and not sys.flags.utf8_mode:
        try:
            import _bootlocale  # noqa: F401 — side-effect import on some Pythons
        except ImportError:
            pass
        # Reconfigure stdout/stderr to UTF-8 if possible
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
