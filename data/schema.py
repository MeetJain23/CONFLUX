"""
CONFLUX database schema.

Design principles:
1. Metadata is hand-curated and lives in dedicated tables — it is THE moat.
2. Time-series data (prices, scores) is regenerable from ingestion + scorers.
3. Each vector writes to vector_scores with its own vector_id; never collide.
4. Composite scores live in confluence_scores, computed by the engine layer.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Boolean,
    ForeignKey, Text, UniqueConstraint, Index, create_engine, JSON,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
from pathlib import Path

Base = declarative_base()


# ---------------------------------------------------------------------------
# Layer 1: Stock Metadata Graph
# ---------------------------------------------------------------------------

class Stock(Base):
    """Master table of stocks in the universe."""
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True)
    symbol_nse = Column(String(50), unique=True, nullable=False, index=True)   # e.g. "RELIANCE"
    symbol_yf = Column(String(50), unique=True, nullable=False)                # e.g. "RELIANCE.NS"
    name = Column(String(200), nullable=False)
    sector = Column(String(100))            # broad NSE sector
    sub_sector = Column(String(100))        # finer industry
    market_cap_cr = Column(Float)           # in crores INR, updated periodically
    in_nifty50 = Column(Boolean, default=False)
    in_nifty100 = Column(Boolean, default=False)
    in_nifty500 = Column(Boolean, default=False)
    promoter_group = Column(String(200))    # e.g. "Tata Sons", "Mukesh Ambani family"
    global_parent = Column(String(200))     # for V11 — e.g. "Hitachi Ltd (Japan)"
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Commodity(Base):
    """Master table of tracked commodities."""
    __tablename__ = "commodities"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)   # e.g. "CRUDE_BRENT"
    name = Column(String(200), nullable=False)
    unit = Column(String(50))                                # e.g. "USD/barrel"
    yf_ticker = Column(String(50))                           # yfinance ticker if available
    category = Column(String(50))                            # energy / metal / agri / chem
    active = Column(Boolean, default=True)


class StockInputCommodity(Base):
    """
    Links a stock to its key input commodities.
    Weight = approximate % of COGS this commodity represents (best-effort).
    """
    __tablename__ = "stock_input_commodities"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    weight_pct = Column(Float)               # 0–100, can be approx
    direction = Column(String(10))           # "negative" (input cost up = bad) usually
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("stock_id", "commodity_id", name="uq_stock_input_commodity"),
    )


class StockOutputCommodity(Base):
    """Links a stock to commodities/products it sells (for V5)."""
    __tablename__ = "stock_output_commodities"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    weight_pct = Column(Float)
    direction = Column(String(10))           # "positive" usually
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("stock_id", "commodity_id", name="uq_stock_output_commodity"),
    )


class StockCustomer(Base):
    """For V7 — who buys this company's products."""
    __tablename__ = "stock_customers"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    customer_name = Column(String(200), nullable=False)
    customer_stock_id = Column(Integer, ForeignKey("stocks.id"))  # if listed
    sector = Column(String(100))
    revenue_share_pct = Column(Float)                              # if known
    notes = Column(Text)


class StockSupplier(Base):
    """For V6 — who supplies this company."""
    __tablename__ = "stock_suppliers"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    supplier_name = Column(String(200), nullable=False)
    supplier_stock_id = Column(Integer, ForeignKey("stocks.id"))
    origin = Column(String(50))                                    # "domestic" / "import"
    notes = Column(Text)


# ---------------------------------------------------------------------------
# Layer 2: Time-series data from ingestion
# ---------------------------------------------------------------------------

class PriceDaily(Base):
    """Daily OHLCV for stocks."""
    __tablename__ = "price_daily"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)

    __table_args__ = (
        UniqueConstraint("stock_id", "date", name="uq_price_daily_stock_date"),
        Index("ix_price_daily_date", "date"),
    )


class CommodityDaily(Base):
    """Daily price for commodities."""
    __tablename__ = "commodity_daily"

    id = Column(Integer, primary_key=True)
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    date = Column(Date, nullable=False)
    close = Column(Float)
    source = Column(String(50))         # "yfinance" / "fred" / etc.

    __table_args__ = (
        UniqueConstraint("commodity_id", "date", name="uq_commodity_daily"),
        Index("ix_commodity_daily_date", "date"),
    )


class MacroDaily(Base):
    """Macroeconomic series: FX, rates, inflation, tariffs — for V13."""
    __tablename__ = "macro_daily"

    id = Column(Integer, primary_key=True)
    series_code = Column(String(50), nullable=False)   # "USDINR", "US10Y", "INDIA10Y", "DXY"
    date = Column(Date, nullable=False)
    value = Column(Float)
    source = Column(String(50))

    __table_args__ = (
        UniqueConstraint("series_code", "date", name="uq_macro_daily"),
        Index("ix_macro_daily_series", "series_code"),
    )


# ---------------------------------------------------------------------------
# Layer 3: Vector scores
# ---------------------------------------------------------------------------

class VectorScore(Base):
    """
    One row per (stock, vector, date).
    score in [-1.0, +1.0]. confidence in [0.0, 1.0] — used in confluence weighting.
    """
    __tablename__ = "vector_scores"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    vector_id = Column(Integer, nullable=False)        # 1..15 matching the framework
    date = Column(Date, nullable=False)
    score = Column(Float, nullable=False)
    confidence = Column(Float, default=1.0)
    rationale = Column(Text)                           # human-readable why
    components_json = Column(Text)                     # raw signals that fed the score

    __table_args__ = (
        UniqueConstraint("stock_id", "vector_id", "date", name="uq_vector_score"),
        Index("ix_vector_scores_date", "date"),
        Index("ix_vector_scores_stock_vector", "stock_id", "vector_id"),
    )


# ---------------------------------------------------------------------------
# Layer 4: Confluence
# ---------------------------------------------------------------------------

class ConfluenceScore(Base):
    """
    Composite score per stock per day, computed from active vector scores.
    Stored so the dashboard reads instantly.
    """
    __tablename__ = "confluence_scores"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    date = Column(Date, nullable=False)
    composite = Column(Float, nullable=False)          # weighted average of vectors
    n_vectors_positive = Column(Integer)
    n_vectors_negative = Column(Integer)
    n_vectors_active = Column(Integer)
    direction = Column(String(10))                     # "bullish" / "bearish" / "neutral"
    vector_breakdown_json = Column(Text)               # per-vector dict for drill-down

    __table_args__ = (
        UniqueConstraint("stock_id", "date", name="uq_confluence"),
        Index("ix_confluence_date", "date"),
    )


# ---------------------------------------------------------------------------
# Pipeline run log — observability matters
# ---------------------------------------------------------------------------

class IngestionRun(Base):
    """Logs every ingestion or scorer run for debugging."""
    __tablename__ = "ingestion_runs"

    id = Column(Integer, primary_key=True)
    job_name = Column(String(100), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    status = Column(String(20))               # "success" / "failure" / "partial"
    rows_written = Column(Integer)
    error_message = Column(Text)

class CorporateAction(Base):
    """
    Discrete corporate event affecting a stock's valuation.
    
    Source: NSE/BSE corporate actions feeds (Phase 1: NSE only).
    Used by V12 (Re-rating Catalysts) scorer.
    
    action_type values (controlled vocabulary):
        BUYBACK, DEMERGER, BONUS, SPLIT, SPECIAL_DIVIDEND,
        BOARD_CHANGE_POSITIVE, BOARD_CHANGE_NEGATIVE,
        PROMOTER_PLEDGE_INCREASE, PROMOTER_PLEDGE_DECREASE,
        OTHER
    
    Idempotency: unique on (stock_id, action_type, action_date).
    """
    __tablename__ = "corporate_actions"
    
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    
    exchange = Column(String(8), nullable=False)  # NSE or BSE
    action_type = Column(String(32), nullable=False, index=True)
    action_date = Column(Date, nullable=False, index=True)
    ex_date = Column(Date, nullable=True)
    
    # Optional override of the default magnitude from event_magnitudes table.
    # Use when an event has unusual size (e.g., buyback at 20% of mcap vs typical 5%).
    magnitude_override = Column(Float, nullable=True)
    
    raw_payload = Column(JSON, nullable=True)  # full response from exchange API
    source_url = Column(String(512), nullable=True)
    notes = Column(String(512), nullable=True)
    
    ingested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    stock = relationship("Stock", backref="corporate_actions")
    
    __table_args__ = (
        UniqueConstraint("stock_id", "action_type", "action_date",
                         name="uq_corp_action_stock_type_date"),
        Index("ix_corp_action_date_type", "action_date", "action_type"),
    )
    
    def __repr__(self):
        return f"<CorporateAction {self.action_type} for stock_id={self.stock_id} on {self.action_date}>"

# ---------------------------------------------------------------------------
# DB initialization
# ---------------------------------------------------------------------------

def get_engine(db_path: str = "data/conflux.db"):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(db_path: str = "data/conflux.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(db_path: str = "data/conflux.db"):
    engine = get_engine(db_path)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


if __name__ == "__main__":
    init_db()
    print("✓ CONFLUX DB initialized at data/conflux.db")
