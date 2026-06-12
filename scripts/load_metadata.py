"""
Load filled-in metadata CSVs into the DB.

Usage:
    1. First generate templates:  python -m metadata.templates
    2. Edit metadata/*.csv (or via Google Sheets → export as CSV → drop in metadata/)
    3. python -m scripts.load_metadata
"""

import csv
import os
import logging

from data.schema import (
    Stock, Commodity, StockInputCommodity, init_db, get_session,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _bool(s: str) -> bool:
    return str(s).strip().upper() in ("TRUE", "1", "YES", "Y")


def load_stocks(path: str, session):
    if not os.path.exists(path):
        logger.warning(f"missing {path}; skipping stocks")
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            existing = session.query(Stock).filter_by(symbol_nse=row["symbol_nse"]).first()
            if existing:
                # update in place
                for k in ("symbol_yf", "name", "sector", "sub_sector",
                          "promoter_group", "global_parent", "notes"):
                    if row.get(k):
                        setattr(existing, k, row[k])
                existing.market_cap_cr = float(row.get("market_cap_cr") or 0) or existing.market_cap_cr
                existing.in_nifty50 = _bool(row.get("in_nifty50"))
                existing.in_nifty100 = _bool(row.get("in_nifty100"))
                existing.in_nifty500 = _bool(row.get("in_nifty500"))
            else:
                session.add(Stock(
                    symbol_nse=row["symbol_nse"],
                    symbol_yf=row["symbol_yf"],
                    name=row["name"],
                    sector=row.get("sector"),
                    sub_sector=row.get("sub_sector"),
                    market_cap_cr=float(row.get("market_cap_cr") or 0),
                    in_nifty50=_bool(row.get("in_nifty50")),
                    in_nifty100=_bool(row.get("in_nifty100")),
                    in_nifty500=_bool(row.get("in_nifty500")),
                    promoter_group=row.get("promoter_group"),
                    global_parent=row.get("global_parent"),
                    notes=row.get("notes"),
                ))
            n += 1
    session.commit()
    logger.info(f"loaded {n} stocks from {path}")
    return n


def load_commodities(path: str, session):
    if not os.path.exists(path):
        logger.warning(f"missing {path}; skipping commodities")
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            existing = session.query(Commodity).filter_by(code=row["code"]).first()
            if existing:
                for k in ("name", "unit", "yf_ticker", "category"):
                    if row.get(k):
                        setattr(existing, k, row[k])
                existing.active = _bool(row.get("active", "TRUE"))
            else:
                session.add(Commodity(
                    code=row["code"], name=row["name"], unit=row.get("unit"),
                    yf_ticker=row.get("yf_ticker"), category=row.get("category"),
                    active=_bool(row.get("active", "TRUE")),
                ))
            n += 1
    session.commit()
    logger.info(f"loaded {n} commodities from {path}")
    return n


def load_input_links(path: str, session):
    if not os.path.exists(path):
        logger.warning(f"missing {path}; skipping input links")
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stock = session.query(Stock).filter_by(symbol_nse=row["symbol_nse"]).first()
            commodity = session.query(Commodity).filter_by(code=row["commodity_code"]).first()
            if not stock or not commodity:
                logger.warning(f"skip link: stock={row['symbol_nse']} commodity={row['commodity_code']}")
                continue
            # Schema migration: CSV now uses cogs_weight_pct (was weight_pct).
            # Direction column dropped from CSV — producers excluded from V4 instead.
            # DB column `direction` retained for backward compatibility, defaulted to "negative".
            weight_value = row.get("cogs_weight_pct") or row.get("weight_pct") or 0
            existing = (
                session.query(StockInputCommodity)
                .filter_by(stock_id=stock.id, commodity_id=commodity.id)
                .first()
            )
            if existing:
                existing.weight_pct = float(weight_value)
                existing.direction = "negative"
                existing.notes = row.get("notes")
            else:
                session.add(StockInputCommodity(
                    stock_id=stock.id, commodity_id=commodity.id,
                    weight_pct=float(weight_value),
                    direction="negative",
                    notes=row.get("notes"),
                ))
            n += 1
    session.commit()
    logger.info(f"loaded {n} input-commodity links from {path}")
    return n


def main():
    init_db()
    session = get_session()
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata")
    load_stocks(os.path.join(base, "stocks.csv"), session)
    load_commodities(os.path.join(base, "commodities.csv"), session)
    load_input_links(os.path.join(base, "stock_input_commodities.csv"), session)
    logger.info("done.")


if __name__ == "__main__":
    main()
