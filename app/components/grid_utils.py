"""
Zamboni — Grid/Pagination Utilities
Shared helpers for displaying large data tables with row count info
and page size control.

st.dataframe has built-in virtual scrolling — no true server-side pagination
needed. We provide:
  - Row count caption
  - Page size selector (25/50/100/250/500)
  - "Showing N of M total" indicator when filtered

Usage:
    from app.components.grid_utils import render_grid, page_size_selector

    limit = page_size_selector(key="my_grid")
    df = load_data(limit=limit)
    render_grid(df, total_count=total_count, key="my_grid")
"""
from __future__ import annotations

import streamlit as st


def page_size_selector(
    key:     str = "grid",
    default: int = 100,
    options: list[int] | None = None,
    label:   str = "Rows per page",
) -> int:
    """
    Render a compact row-count selector. Returns the selected page size.
    Place this before your data query to use as the LIMIT.
    """
    if options is None:
        options = [25, 50, 100, 250, 500]
    idx = options.index(default) if default in options else 2
    return st.selectbox(
        label,
        options,
        index=idx,
        key=f"{key}_page_size",
        help="Number of rows to display. "
             "st.dataframe supports virtual scrolling — all rows are searchable.",
    )


def render_grid(
    df,
    total_count:   int | None = None,
    key:           str = "grid",
    height:        int = 420,
    show_caption:  bool = True,
    hide_index:    bool = True,
) -> None:
    """
    Render a dataframe with row count info.

    Args:
        df:           DataFrame to display (already limited to page_size).
        total_count:  Total rows available (before LIMIT). If None, uses len(df).
        key:          Unique widget key prefix.
        height:       Pixel height of the grid.
        show_caption: Whether to show the row count line below the grid.
    """
    import pandas as pd

    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        st.info("No data to display.")
        return

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=hide_index,
        height=height,
    )

    if show_caption:
        shown = len(df)
        total = total_count if total_count is not None else shown
        if total > shown:
            st.caption(
                f"Showing **{shown:,}** of **{total:,}** rows "
                f"— increase *Rows per page* to see more."
            )
        else:
            st.caption(f"{shown:,} row(s)")
