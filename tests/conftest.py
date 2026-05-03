"""
Zamboni -- pytest conftest
1. Sets ZAMBONI_TEST_MODE=true so settings.py uses safe mock defaults.
2. Sets PYTHONUTF8=1 to prevent cp1252 errors on Windows.
3. Provides a lightweight streamlit stub so app/components/* can be
   imported in unit tests without a running Streamlit server.
"""
import os
import sys
import types
from unittest.mock import MagicMock


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

    # ── Streamlit stub ────────────────────────────────────────────────────────
    # Inject a minimal streamlit mock so app/components/* can be imported
    # in unit tests without ModuleNotFoundError or running a server.
    # Tests that need real Streamlit behavior should live in tests/integration/.
    if "streamlit" not in sys.modules:
        _st = types.ModuleType("streamlit")

        # Common widgets + functions that return safe defaults
        for _fn in [
            "text_input", "text_area", "number_input", "selectbox",
            "multiselect", "checkbox", "button", "form_submit_button",
            "download_button", "slider", "radio", "date_input",
            "columns", "expander", "tabs", "container", "sidebar",
            "spinner", "progress", "empty",
            "set_page_config", "stop", "rerun",
        ]:
            setattr(_st, _fn, MagicMock(return_value=MagicMock()))

        for _fn in ["write", "markdown", "title", "header", "subheader",
                    "caption", "code", "json", "divider",
                    "success", "error", "warning", "info",
                    "dataframe", "table", "metric", "image"]:
            setattr(_st, _fn, MagicMock())

        # columns() must return an iterable of context managers
        def _columns(*args, **kwargs):
            n = args[0] if args else 2
            if isinstance(n, int):
                return [MagicMock(__enter__=lambda s, *a: s,
                                  __exit__=lambda s, *a: None)
                        for _ in range(n)]
            return [MagicMock(__enter__=lambda s, *a: s,
                              __exit__=lambda s, *a: None)
                    for _ in range(len(n))]
        _st.columns = _columns

        # tabs() returns list of context managers
        def _tabs(labels):
            return [MagicMock(__enter__=lambda s, *a: s,
                              __exit__=lambda s, *a: None)
                    for _ in labels]
        _st.tabs = _tabs

        # expander() is a context manager
        def _expander(*a, **kw):
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__  = lambda s, *a: None
            return m
        _st.expander = _expander

        # session_state as a simple dict-like
        _st.session_state = {}

        # cache_data decorator — pass-through
        def _cache_data(fn=None, **kw):
            if fn is not None:
                return fn
            return lambda f: f
        _st.cache_data = _cache_data

        # form as context manager
        def _form(*a, **kw):
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__  = lambda s, *a: None
            return m
        _st.form = _form

        sys.modules["streamlit"] = _st

    # Also stub streamlit.cache_data if needed as a submodule reference
    if "streamlit.components" not in sys.modules:
        _stc = types.ModuleType("streamlit.components")
        sys.modules["streamlit.components"] = _stc
