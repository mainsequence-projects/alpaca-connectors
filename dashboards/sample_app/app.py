# dashboards/sample_app/app.py
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

from mainsequence import logger

# Ensure repo root is importable (same as before)
ROOT = Path(__file__).resolve().parent
if str(ROOT.parent.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent.parent))


logger.info("Starting Main Sequence Streamlit minimal app...")

# The legacy `mainsequence.dashboards.streamlit.scaffold` (PageConfig / run_page) was removed in
# SDK 4.x and has no replacement in the current SDK. Configure the page directly with Streamlit,
# preserving the original title and wide-layout intent.
st.set_page_config(page_title="Main Sequence Demo App", layout="wide")

st.caption("Sample Streamlit running in Main Sequence ")
