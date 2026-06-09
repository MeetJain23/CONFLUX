"""
CONFLUX dashboard — Streamlit.
Run: streamlit run app/dashboard.py

Phase 1 view: Top setups ranked by composite score.
"""

from datetime import date as date_type, timedelta

import pandas as pd
import streamlit as st

from data.schema import Stock, ConfluenceScore, get_session


st.set_page_config(page_title="CONFLUX", layout="wide")


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
            "Symbol": s.symbol_nse, "Name": s.name, "Sector": s.sector,
            "Composite": round(c.composite, 3), "Direction": c.direction,
            "+Vectors": c.n_vectors_positive, "-Vectors": c.n_vectors_negative,
            "Active": c.n_vectors_active,
        }
        for c, s in rows
    ])


st.title("CONFLUX")
st.caption("Confluence Of Numerous Factors Linking Underlying eXposures")

with st.sidebar:
    asof = st.date_input("As-of date", value=date_type.today() - timedelta(days=1))
    direction = st.radio("Direction filter", ["bullish", "bearish", "all"], horizontal=True)
    min_active = st.slider("Min active vectors", 1, 15, 1)

df = load_top_setups(asof, direction, min_active)
if df.empty:
    st.warning("No data for this date yet. Run `python -m scripts.run_daily` to populate.")
else:
    st.dataframe(df, use_container_width=True, hide_index=True)
