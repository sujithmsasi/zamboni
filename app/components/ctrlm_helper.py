"""
Zamboni — Control-M Job Name Input Helper

Provides a combined selectbox+text_input widget:
- Loads job names from controlm_jobs registry
- Shows as searchable selectbox when jobs exist
- Falls back to plain text input if no registry jobs
- "Enter manually" option always available

Usage:
    from app.components.ctrlm_helper import ctrlm_job_input

    job_name = ctrlm_job_input(
        label="Control-M Job Name",
        value=current_value,
        key="my_job_key",
    )
"""
from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=120, show_spinner=False)
def _get_registered_jobs() -> list[str]:
    """Load job names from controlm_jobs table."""
    try:
        from app.components.athena_runner import cached_read_registry
        df = cached_read_registry(
            "SELECT job_name FROM controlm_jobs "
            "WHERE active = 1 ORDER BY job_name"
        )
        return df["job_name"].tolist() if not df.empty else []
    except Exception:
        return []


def ctrlm_job_input(
    label: str,
    value: str = "",
    key: str = "ctrlm_job",
    placeholder: str = "ACE-DA-FIN-APS-INGEST-PRD",
    help: str = "",
    required: bool = False,
) -> str:
    """
    Render a Control-M job name input with registry suggestions.

    If jobs are registered in the Control-M Job Registry, shows a
    searchable selectbox with "— enter manually —" option.
    Falls back to plain text input if registry is empty.

    Returns the selected or entered job name string.
    """
    _lbl = f"{label} *" if required else label
    _jobs = _get_registered_jobs()

    if not _jobs:
        # No registry yet — plain text input
        return st.text_input(
            _lbl,
            value=value,
            key=key,
            placeholder=placeholder,
            help=help + (" Add jobs in Bulk Control-M → Job Registry tab." if not help else ""),
        )

    # Build options: blank, registered jobs, then manual entry option
    _MANUAL = "✏️ Enter manually…"
    _options = [""] + _jobs + [_MANUAL]

    # Determine default index: match current value if in list
    _cur = str(value or "").strip()
    if _cur in _jobs:
        _idx = _options.index(_cur)
    else:
        _idx = 0

    _sel = st.selectbox(
        _lbl,
        _options,
        index=_idx,
        key=f"{key}_sel",
        help=(help or "") + f"  {len(_jobs)} jobs in registry. "
             "Select or choose '✏️ Enter manually…' to type a new name.",
        format_func=lambda x: x if x else "— select or type below —",
    )

    if _sel == _MANUAL or (_cur and _cur not in _jobs and _idx == 0):
        # Show text input for manual entry
        return st.text_input(
            f"{_lbl} (manual)",
            value=_cur if _cur not in _jobs else "",
            key=f"{key}_manual",
            placeholder=placeholder,
            help="Type the exact job name. Add it to the registry for future use.",
        )

    return _sel
