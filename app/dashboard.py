"""
CONFLUX dashboard — Streamlit.

Run:
    streamlit run app/dashboard.py

Phase 1 views:
    - Top setups today (ranked by composite, filterable bullish/bearish)
    - Per-stock drill-down: which vectors are firing, with rationale
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import date as date_type, timedelta

import pandas as pd
import streamlit as st

from data.schema import Stock, ConfluenceScore, VectorScore, get_session

from dotenv import load_dotenv
load_dotenv()

from data.db_provisioner import ensure_db_exists, DBProvisionError


@st.cache_resource(show_spinner="Loading database...")
def _provision_db():
    """
    Provision the DB on container startup. Cached for the container's lifetime
    so we don't re-download on every page reload.
    
    Returns the path to the DB. If provisioning fails, displays a Streamlit
    error and stops execution.
    """
    try:
        return ensure_db_exists()
    except DBProvisionError as e:
        st.error(f"Cannot start dashboard: {e}")
        st.stop()


# Call once at module load to ensure DB exists before any get_session() call
_provision_db()


st.set_page_config(page_title="CONFLUX", layout="wide")

VECTOR_NAMES = {
    1: "Promoters",
    2: "Govt Policies",
    3: "Holding Cos",
    4: "Input Material Cost",
    5: "Output Material Cost",
    6: "Input Supply Side",
    7: "Output Demand Side",
    8: "Services Companies",
    9: "Global Capex Focus",
    10: "User Behaviour",
    11: "Global Parallels",
    12: "Re-rating",
    13: "Geopolitics & Macros",
    14: "Structural Cycle",
    15: "Moat / Pricing Power",
}


@st.cache_data(ttl=300)
def load_top_setups(asof: date_type, direction: str, min_active_vectors: int):
    session = get_session()
    q = (
        session.query(ConfluenceScore, Stock)
        .join(Stock, Stock.id == ConfluenceScore.stock_id)
        .filter(ConfluenceScore.date == asof)
        .filter(ConfluenceScore.n_vectors_active >= min_active_vectors)
    )
    if direction == "bullish":
        q = q.order_by(ConfluenceScore.composite.desc())
    elif direction == "bearish":
        q = q.order_by(ConfluenceScore.composite.asc())
    else:
        q = q.order_by(ConfluenceScore.composite.desc())
    rows = q.limit(50).all()

    return pd.DataFrame([
        {
            "Symbol": s.symbol_nse,
            "Name": s.name,
            "Sector": s.sector,
            "Composite": round(c.composite, 3),
            "Direction": c.direction,
            "+Vectors": c.n_vectors_positive,
            "-Vectors": c.n_vectors_negative,
            "Active": c.n_vectors_active,
        }
        for c, s in rows
    ])


def render_stock_detail(symbol_nse: str, asof: date_type):
    session = get_session()
    stock = session.query(Stock).filter_by(symbol_nse=symbol_nse).first()
    if not stock:
        st.error("Stock not found")
        return

    st.subheader(f"{stock.symbol_nse} — {stock.name}")
    st.caption(f"{stock.sector} / {stock.sub_sector or '—'}")

    conf = (
        session.query(ConfluenceScore)
        .filter_by(stock_id=stock.id, date=asof).first()
    )
    if conf:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Composite", f"{conf.composite:+.3f}")
        col2.metric("Direction", conf.direction)
        col3.metric("Positive vectors", conf.n_vectors_positive)
        col4.metric("Negative vectors", conf.n_vectors_negative)

    vs = (
        session.query(VectorScore)
        .filter_by(stock_id=stock.id, date=asof)
        .order_by(VectorScore.vector_id)
        .all()
    )
    if not vs:
        st.info("No vector scores yet for this date.")
        return

    st.markdown("### Vector breakdown")
    for v in vs:
        with st.expander(
            f"V{v.vector_id} — {VECTOR_NAMES.get(v.vector_id, '?')} "
            f"| score {v.score:+.3f} (conf {v.confidence:.2f})"
        ):
            st.write(v.rationale)
            if v.components_json:
                try:
                    st.json(json.loads(v.components_json))
                except json.JSONDecodeError:
                    st.code(v.components_json)


# ---------------- UI ----------------
st.title("CONFLUX")
st.caption("Confluence Of Numerous Factors Linking Underlying eXposures")

with st.sidebar:
    asof = st.date_input("As-of date", value=date_type.today())
    direction = st.radio("Direction filter", ["bullish", "bearish", "all"], horizontal=True)
    min_active = st.slider("Min active vectors", 1, 15, 1)

tab1, tab2 = st.tabs(["Top setups", "Stock drill-down"])

with tab1:
    df = load_top_setups(asof, direction, min_active)
    if df.empty:
        st.warning(
            "No data for this date yet. Run `python -m scripts.run_daily` to populate."
        )
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

with tab2:
    symbol = st.text_input("NSE symbol (e.g. RELIANCE)").strip().upper()
    if symbol:
        render_stock_detail(symbol, asof)
