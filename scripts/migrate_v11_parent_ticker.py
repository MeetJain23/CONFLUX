"""
V11 migration: add Stock.parent_ticker column + populate for the 16 stocks
with public global parent listings.

Run once. Idempotent — re-runs are no-ops.

  python -m scripts.migrate_v11_parent_ticker
"""

import logging
from sqlalchemy import text, inspect

from data.schema import get_session, Stock

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PARENT_MAPPINGS = [
    ("SIEMENS",     "SIE.DE"),
    ("ABB",         "ABBN.SW"),
    ("POWERINDIA",  "6501.T"),
    ("HONAUT",      "HON"),
    ("3MINDIA",     "MMM"),
    ("CUMMINSIND",  "CMI"),
    ("HYUNDAI",     "005380.KS"),
    ("MARUTI",      "7269.T"),
    ("HINDUNILVR",  "ULVR.L"),
    ("NESTLEIND",   "NESN.SW"),
    ("ITC",         "BATS.L"),
    ("COLPAL",      "CL"),
    ("ICICIPRULI",  "PRU.L"),
    ("BHARTIARTL",  "Z74.SI"),
    ("IDEA",        "VOD.L"),
    ("MOTHERSON",   "5802.T"),
]


def main():
    session = get_session()
    engine = session.get_bind()
    inspector = inspect(engine)

    existing_cols = [c["name"] for c in inspector.get_columns("stocks")]
    if "parent_ticker" not in existing_cols:
        logger.info("Adding parent_ticker column to stocks table")
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE stocks ADD COLUMN parent_ticker VARCHAR"))
            conn.commit()
    else:
        logger.info("parent_ticker column already exists, skipping ALTER")

    updated, missing = 0, []
    for symbol, ticker in PARENT_MAPPINGS:
        stock = session.query(Stock).filter_by(symbol_nse=symbol).first()
        if not stock:
            missing.append(symbol)
            continue
        if stock.parent_ticker != ticker:
            stock.parent_ticker = ticker
            updated += 1
    session.commit()

    logger.info(f"Populated parent_ticker for {updated} stocks")
    if missing:
        logger.warning(f"Stocks not found in DB: {missing}")


if __name__ == "__main__":
    main()