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
    ForeignKey, Text, UniqueConstraint, Index, create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from pathlib import Path

Base = declarative_base()


# ---------------------------------------------------------------------------
# Layer 1: Master tables
# ---------------------------------------------------------------------------

class Stock(Base):
    __tablename__ = "stocks"
    id = Column(Integer, primary_key=True)
    symbol_nse = Column(String(50), unique=True, nullable=False, index=True)
    symbol_yf = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    sector = Column(String(100))
    sub_sector = Column(String(100))
    market_cap_cr = Column(Float)
    in_nifty50 = Column(Boolean, default=False)
    in_nifty100 = Column(Boolean, default=False)
    in_nifty500 = Column(Boolean, default=False)
    promoter_group = Column(String(200))
    global_parent = Column(String(200))
    active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Commodity(Base):
    __tablename__ = "commodities"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    unit = Column(String(50))
    yf_ticker = Column(String(50))
    category = Column(String(50))
    active = Column(Boolean, default=True)


# ---------------------------------------------------------------------------
# Layer 1b: Metadata graph relationship tables
# ---------------------------------------------------------------------------

class StockInputCommodity(Base):
    """Links a stock to its key input commodities.
    Weight = approximate % of COGS this commodity represents."""
    __tablename__ = "stock_input_commodities"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    weight_pct = Column(Float)
    direction = Column(String(10))
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("stock_id", "commodity_id", name="uq_stock_input_commodity"),
    )


class StockOutputCommodity(Base):
    """For V5: commodities/products a stock sells."""
    __tablename__ = "stock_output_commodities"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    weight_pct = Column(Float)
    direction = Column(String(10))
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("stock_id", "commodity_id", name="uq_stock_output_commodity"),
    )


class StockCustomer(Base):
    """For V7: who buys this company's products."""
    __tablename__ = "stock_customers"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    customer_name = Column(String(200), nullable=False)
    customer_stock_id = Column(Integer, ForeignKey("stocks.id"))
    sector = Column(String(100))
    revenue_share_pct = Column(Float)
    notes = Column(Text)


class StockSupplier(Base):
    """For V6: who supplies this company."""
    __tablename__ = "stock_suppliers"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    supplier_name = Column(String(200), nullable=False)
    supplier_stock_id = Column(Integer, ForeignKey("stocks.id"))
    origin = Column(String(50))
    notes = Column(Text)


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
