"""
Daily runner: one command to refresh CONFLUX.

Usage:
    python -m scripts.run_daily            # today's date
    python -m scripts.run_daily 2026-05-22 # specific date

Order:
    1. ingest stock prices, commodity prices, macros
    2. run all active Phase 1 scorers (V4, V13) for the date
    3. compute confluence for the date
"""

import sys
import logging
from datetime import date as date_type

from data.schema import Stock, init_db, get_session
from ingestion.prices import ingest_stock_prices, ingest_commodity_prices, ingest_macros
from scorers.v04_input_material_cost import InputMaterialCostScorer
from scorers.v13_macros import MacroScorer
from confluence.engine import compute_confluence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("conflux.daily")


def parse_asof(argv):
    if len(argv) > 1:
        return date_type.fromisoformat(argv[1])
    return date_type.today()


def main():
    asof = parse_asof(sys.argv)
    logger.info(f"=== CONFLUX daily run for {asof} ===")

    init_db()
    session = get_session()

    # 1. Ingestion
    logger.info("[1/3] ingestion")
    ingest_stock_prices(session=session)
    ingest_commodity_prices(session=session)
    ingest_macros(session=session)

    # 2. Scorers
    logger.info("[2/3] scorers")
    stocks = session.query(Stock).filter(Stock.active.is_(True), Stock.in_nifty500.is_(True)).all()
    logger.info(f"Universe: {len(stocks)} active Nifty 500 stocks")

    v4 = InputMaterialCostScorer(session=session)
    v4_results = v4.score_universe(stocks, asof)
    v4.write_scores(v4_results, asof)

    v13 = MacroScorer(session=session)
    v13_results = v13.score_universe(stocks, asof)
    v13.write_scores(v13_results, asof)

    # 3. Confluence
    logger.info("[3/3] confluence")
    compute_confluence(asof, session=session)

    logger.info("=== done ===")


if __name__ == "__main__":
    main()
