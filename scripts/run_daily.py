"""
Daily runner: one command to refresh CONFLUX.

Usage:
    python -m scripts.run_daily            # today's date
    python -m scripts.run_daily 2026-05-22 # specific date

Order:
    1. ingest stock prices, commodity prices, macros, India 10Y (FRED),
       and corporate actions (NSE)
    2. run all active scorers (V4, V12, V13) for the date
    3. compute confluence for the date
"""

import sys
import logging
from datetime import date as date_type

from data.schema import Stock, init_db, get_session
from ingestion.prices import (
    ingest_stock_prices, ingest_commodity_prices, ingest_macros, ingest_india_10y,
)
from ingestion.corporate_actions import ingest_corporate_actions
from scorers.v04_input_material_cost import InputMaterialCostScorer
from scorers.v13_macros import MacroScorer
from scorers.v12_rerating import RerateCatalystScorer
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
    ingest_india_10y(session=session)
    
    # Corporate actions: failure-aware. Logs to ingestion_runs regardless of
    # outcome. V12 scorer will detect stale ingestion via that log and
    # withhold scores rather than lie about coverage.
    try:
        ca_summary = ingest_corporate_actions(session=session)
        logger.info(
            f"Corporate actions: ingested={ca_summary['ingested']}, "
            f"skipped_no_stock={ca_summary['skipped_no_stock']}, "
            f"skipped_routine={ca_summary['skipped_routine']}"
        )
    except Exception as e:
        # We catch broadly here because the NSE endpoint is unofficial and
        # can fail in unexpected ways. V12 will gracefully degrade — it
        # checks ingestion freshness and returns confidence=0 if stale.
        logger.exception(f"Corporate actions ingestion failed: {e}")
        logger.warning(
            "Continuing daily run without corporate actions refresh. "
            "V12 will report stale-ingestion status until next successful run."
        )
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

    v12 = RerateCatalystScorer(session=session)
    v12_results = v12.score_universe(stocks, asof)
    v12.write_scores(v12_results, asof)

    # 3. Confluence
    logger.info("[3/3] confluence")
    compute_confluence(asof, session=session)

    logger.info("=== done ===")


if __name__ == "__main__":
    main()
