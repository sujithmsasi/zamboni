"""
Zamboni — Reusable Filter Components
Domain / Layer / Tier / Environment dropdowns used across multiple pages.
"""
import streamlit as st

from app.components.athena_runner import cached_read_registry
from config.settings import VALID_ENVIRONMENTS, VALID_LAYERS, VALID_TIERS


def domain_filter(label: str = "Domain", include_all: bool = True, key: str = "domain_filter"):
    """Return selected domain or None."""
    domains = _get_domain_list()
    options = (["All"] if include_all else []) + domains
    selected = st.selectbox(label, options, key=key)
    return None if selected == "All" else selected


def layer_filter(label: str = "Layer", include_all: bool = True, key: str = "layer_filter"):
    options = (["All"] if include_all else []) + VALID_LAYERS
    selected = st.selectbox(label, options, key=key)
    return None if selected == "All" else selected


def tier_filter(label: str = "Tier", include_all: bool = True, key: str = "tier_filter"):
    options = (["All"] if include_all else []) + VALID_TIERS
    selected = st.selectbox(label, options, key=key)
    return None if selected == "All" else selected


def environment_filter(label: str = "Environment", default: str = "prod", key: str = "env_filter"):
    return st.selectbox(label, VALID_ENVIRONMENTS, index=VALID_ENVIRONMENTS.index(default), key=key)


def _get_domain_list() -> list[str]:
    """Fetch active domains from domain_registry."""
    try:
        from config.settings import DOMAIN_REGISTRY_TABLE
        sql = f"SELECT domain_name FROM {DOMAIN_REGISTRY_TABLE} WHERE is_active = 1 ORDER BY domain_name"
        df  = cached_read_registry(sql)
        return df["domain_name"].tolist()
    except Exception:
        # Fallback static list in case registry isn't queryable
        return ["finance", "ers", "membership", "claims", "travel", "financials"]
