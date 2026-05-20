"""
Zamboni — Grid/Pagination Utilities
Uses itables (DataTables.js) for interactive, client-side searchable/sortable
paginated grids. Falls back to st.dataframe if itables unavailable.

Usage:
    from app.components.grid_utils import render_grid

    render_grid(df, key="my_grid")
    render_grid(df, key="my_grid", page_length=50)
"""
from __future__ import annotations

import streamlit as st

_ITABLES_AVAILABLE = False
try:
    from itables.streamlit import interactive_table as _it
    _ITABLES_AVAILABLE = True
except ImportError:
    pass


def render_grid(
    df,
    key:         str = "grid",
    page_length: int = 50,
    height:      int = 0,
    caption:     str | None = None,
    hide_index:  bool = True,
    show_count:  bool = True,
) -> None:
    """
    Render an interactive data grid using itables (DataTables.js) or st.dataframe.

    itables features:
      - Client-side search across all columns
      - Sortable columns
      - Configurable page length (25/50/100/250/All)
      - Export buttons (CSV, Excel, PDF)
      - Works with 10K+ rows via client-side virtual rendering

    Args:
        df:          DataFrame to display.
        key:         Unique widget key.
        page_length: Default rows per page (25/50/100/250 or -1 for All).
        height:      Ignored when using itables (it auto-sizes).
        caption:     Optional caption below the grid.
        hide_index:  Whether to hide the index column.
        show_count:  Whether to show row count caption.
    """
    import pandas as pd
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        st.info("No data to display.")
        return

    if hide_index and isinstance(df, pd.DataFrame):
        df = df.reset_index(drop=True)

    if show_count and caption is None:
        caption = f"{len(df):,} row(s)"

    if _ITABLES_AVAILABLE:
        _it(
            df,
            key=key,
            style="width:100%;font-size:12px;",
            classes="display compact stripe hover nowrap",
            lengthMenu=[[25, 50, 100, 250, -1], ["25", "50", "100", "250", "All"]],
            pageLength=page_length,
            scrollX=True,
            columnDefs=[
                {"className": "dt-center", "targets": "_all"},
                {"className": "dt-left",   "targets": [0]},
            ],
            caption=caption,
        )
    else:
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=hide_index,
            height=max(height, 400) if height else 420,
        )
        if caption:
            st.caption(caption)


def page_size_selector(
    key:     str = "grid",
    default: int = 100,
    options: list[int] | None = None,
    label:   str = "Rows per page",
) -> int:
    """Legacy helper — kept for back-compat. render_grid handles pagination internally."""
    if options is None:
        options = [25, 50, 100, 250, 500]
    idx = options.index(default) if default in options else 2
    return st.selectbox(label, options, index=idx, key=f"{key}_page_size")
