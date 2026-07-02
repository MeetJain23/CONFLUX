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
from datetime import datetime, date, timezone
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
    parent_ticker = Column(String, nullable=True)
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


class InsiderTrade(Base):
    """
    SEBI Reg 7(2) PIT (Prohibition of Insider Trading) continual disclosures.
    
    One row per filing — promoter, director, KMP, or related person buying or
    selling shares in their own company. Source: NSE corporates-pit endpoint.
    
    V1 scorer reads from this table. Scoring weights person_category
    (Promoter > Director > KMP), acq_mode (Market Purchase = full signal,
    ESOP/Inheritance/Gift = near zero), transaction_type (Buy/Sell), and
    magnitude (pct_change in holding + secVal).
    
    Idempotency: (stock_id, pit_id) is the natural key. NSE provides a
    unique 'pid' field per filing; we store it as pit_id and use it for
    dedup.
    """
    __tablename__ = "insider_trades"

    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)

    # NSE filing identifier — used for idempotency
    pit_id = Column(String, nullable=False, index=True)

    # Who filed
    person_name = Column(String, nullable=False)
    person_category = Column(String, nullable=False, index=True)
    # Expected values per SEBI: 'Promoter', 'Promoter Group',
    # 'Immediate Relative of Promoter', 'Director', 'KMP',
    # 'Designated Persons', 'Others'

    # What kind of transaction
    acq_mode = Column(String)
    # Routine: 'ESOP', 'Inheritance', 'Gift', 'Transmission'
    # Opportunistic (high signal): 'Market Purchase', 'Off Market',
    # 'Inter-se Transfer', 'Block Deal', 'Bulk Deal'

    transaction_type = Column(String, nullable=False, index=True)
    # 'Buy' or 'Sell' (from NSE's tdpTransactionType)

    # Size
    securities_qty = Column(Float)
    securities_value = Column(Float)  # in INR
    pct_before = Column(Float)
    pct_after = Column(Float)

    # When
    transaction_date = Column(Date, nullable=False, index=True)
    # NSE provides acqfromDt + acqtoDt; we use acqtoDt as the canonical
    # transaction_date since that's when the position change completed
    intimation_date = Column(Date)
    # When the filing was made — usually 1-2 days after transaction

    # SEBI regulation reference — should be '7(2)' for PIT disclosures
    regulation = Column(String)

    # Audit trail
    exchange = Column(String, default="NSE")
    raw_payload = Column(JSON)  # Full original NSE record for audit/replay
    source_url = Column(String)
    xbrl_url = Column(String)   # Link to underlying SEBI XBRL filing
    notes = Column(String)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        UniqueConstraint("stock_id", "pit_id", name="uq_insider_trade_stock_pit"),
    )

    def __repr__(self):
        return (
            f"<InsiderTrade(stock_id={self.stock_id}, "
            f"person={self.person_name!r}, category={self.person_category!r}, "
            f"type={self.transaction_type!r}, date={self.transaction_date})>"
        )


class PolicyEvent(Base):
    """
    Discrete government policy event affecting one or more stocks.
    
    Source: PIB press releases (RSS) + Google News RSS (curated keywords).
    Used by V02 (Government Policy) scorer.
    
    subtype values (controlled vocabulary, regex-classified from event text):
        PLI_SEMICONDUCTORS, PLI_AUTO_COMPONENTS, PLI_PHARMA, PLI_TEXTILES, ...
        ANTI_DUMPING_STEEL, ANTI_DUMPING_CHEMICALS, ...
        TARIFF_INCREASE_<COMMODITY>, TARIFF_DECREASE_<COMMODITY>, ...
        DUTY_INCREASE_<COMMODITY>, DUTY_DECREASE_<COMMODITY>, ...
        BUDGET_<SECTOR>, GST_<DOMAIN>, PRIVATIZATION_<COMPANY>, ...
        OTHER
    
    Idempotency: unique on (subtype, event_date, source_url).
    Multiple sources may report the same event; source_url disambiguates.
    """
    __tablename__ = "policy_events"
    
    id = Column(Integer, primary_key=True)
    
    subtype = Column(String(64), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    
    # Headline / summary text from the source (for audit + future re-classification)
    headline_text = Column(String(1000), nullable=True)
    
    # Source identification
    source = Column(String(32), nullable=False)  # "PIB" or "GOOGLE_NEWS"
    source_url = Column(String(512), nullable=True)
    
    # Optional override of the default magnitude from policy subtypes table.
    # Used when an event has unusual magnitude (e.g., a much-bigger-than-usual
    # PLI scheme).
    magnitude_override = Column(Float, nullable=True)
    
    raw_payload = Column(JSON, nullable=True)  # full RSS entry / news item
    notes = Column(String(512), nullable=True)
    
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    
    __table_args__ = (
        UniqueConstraint("subtype", "event_date", "source_url",
                         name="uq_policy_event_subtype_date_source"),
        Index("ix_policy_event_date_subtype", "event_date", "subtype"),
    )
    
    def __repr__(self):
        return f"<PolicyEvent {self.subtype} on {self.event_date} from {self.source}>"

class SupplyEvent(Base):
    """
    Discrete supply-side disruption event affecting one or more stocks.
    
    Source: Google News RSS (V0; supply-disruption keyword set), with
    provisions to add specialized sources later (IMD weather portal for
    MONSOON_*, OPEC press releases for OPEC_*, MOSPI gas allocation
    notices for NATURAL_GAS_*, customs data for CHINA_API_*).
    
    Used by V06 (Supply Disruption) scorer.
    
    subtype values (controlled vocabulary, regex-classified from event text):
        SUPPLY_MONSOON_DEFICIT, SUPPLY_MONSOON_NORMAL_ABOVE,
        SUPPLY_HORMUZ_GULF_DISRUPTION, SUPPLY_CHINA_STEEL_CUT,
        SUPPLY_CHINA_API_DUMP, SUPPLY_PHARMA_API_DISRUPTION,
        SUPPLY_CRITICAL_MINERALS_CURB, SUPPLY_SEMICONDUCTOR_SHORTAGE,
        SUPPLY_OPEC_CUT, SUPPLY_NATURAL_GAS_SHORTAGE,
        SUPPLY_GLOBAL_TARIFF_SHOCK, SUPPLY_FORCE_MAJEURE
    
    Idempotency: unique on (subtype, event_date, source_url).
    Multiple outlets may report the same event; source_url disambiguates.
    """
    __tablename__ = "supply_events"
    
    id = Column(Integer, primary_key=True)
    
    subtype = Column(String(64), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    
    # Headline / summary text from the source (for audit + future re-classification)
    headline_text = Column(String(1000), nullable=True)
    
    # Where the disruption originated. Lets calibration distinguish
    # "China API curb" from "Iran-driven Hormuz disruption" even when
    # both classify into the same broad subtype.
    source_country = Column(String(64), nullable=True)
    
    # Scope of impact: "global" / "regional" / "india_specific".
    # MONSOON is india_specific. HORMUZ is regional. OPEC_CUT is global.
    geography_scope = Column(String(32), nullable=True)
    
    # Coarse 0-1 severity extracted at classification time. Optional;
    # used by Phase 2 calibration to scale magnitudes per-event rather
    # than per-subtype.
    event_severity = Column(Float, nullable=True)
    
    # Source identification
    source = Column(String(32), nullable=False)  # "GOOGLE_NEWS" initially
    source_url = Column(String(512), nullable=True)
    
    # Optional per-event magnitude override from default in subtypes table
    magnitude_override = Column(Float, nullable=True)
    
    raw_payload = Column(JSON, nullable=True)
    notes = Column(String(512), nullable=True)
    
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    
    __table_args__ = (
        UniqueConstraint("subtype", "event_date", "source_url",
                         name="uq_supply_event_subtype_date_source"),
        Index("ix_supply_event_date_subtype", "event_date", "subtype"),
    )
    
    def __repr__(self):
        return f"<SupplyEvent {self.subtype} on {self.event_date} from {self.source_country or 'unknown'}>"

        
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
