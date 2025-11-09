import streamlit as st
from typing import List, Dict, Any
from datetime import datetime

# Simple live refresh control using Streamlit's autorefresh

def setup_autorefresh(seconds: int = 120):
    """Enable periodic page refresh to pick up external Notion edits."""
    st.sidebar.checkbox(
        "🔁 Auto-refresh", value=st.session_state.get("auto_refresh", False), key="auto_refresh",
        help=f"If checked, the page refreshes every {seconds} s to reflect external edits."
    )
    if st.session_state.get("auto_refresh"):
        st.experimental_rerun  # noop alias to hint behavior
        st.sidebar.caption("Auto-refresh is on")
        # streamlit has st_autorefresh, avoid import ambiguity by lazy call
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=seconds * 1000, limit=None, key="refresh_key")
        except Exception:
            pass

# Compute diffs for partial updates

def diff_properties(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    changed = {}
    for k, v in new.items():
        if old.get(k) != v:
            changed[k] = v
    return changed
