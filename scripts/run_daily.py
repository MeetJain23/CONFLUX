"""
Daily runner: one command to refresh CONFLUX.

Usage:
    python -m scripts.run_daily              # today's date, local only
    python -m scripts.run_daily 2026-05-22   # specific date, local only
    python -m scripts.run_daily --push       # today's date + push DB to R2
    python -m scripts.run_daily 2026-05-22 --push  # specific date + push

Order:
    1. ingest stock prices, commodity prices, macros, India 10Y (FRED),
       corporate actions (NSE), and policy news (Google News RSS)
    2. run all active scorers (V2, V4, V12, V13) for the date
    3. compute confluence for the date
"""

import argparse
import sys
import logging
from datetime import date as date_type

from data.schema import Stock, init_db, get_session
from ingestion.prices import (
    ingest_stock_prices, ingest_commodity_prices, ingest_macros, ingest_india_10y,
)
from ingestion.parent_prices import ingest_parent_prices
from confluence.engine import compute_confluence
from ingestion.corporate_actions import ingest_corporate_actions
from ingestion.policy_news import ingest_policy_news
from ingestion.insider_trades import ingest_insider_trades
from scorers.v04_input_material_cost import InputMaterialCostScorer
from scorers.v13_macros import MacroScorer
from scorers.v12_rerating import RerateCatalystScorer
from scorers.v02_govt_policy import GovtPolicyScorer
from scorers.v11_global_parallels import GlobalParallelsScorer
from scorers.v01_promoters import PromotersScorer
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("conflux.daily")


def parse_args():
    parser = argparse.ArgumentParser(description="CONFLUX daily refresh")
    parser.add_argument(
        "asof",
        nargs="?",
        default=None,
        help="ISO date (YYYY-MM-DD), defaults to today",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="After local refresh, upload conflux.db to R2 (production refresh)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    asof = date_type.fromisoformat(args.asof) if args.asof else date_type.today()
    logger.info(f"=== CONFLUX daily run for {asof} ===")

    init_db()
    session = get_session()

    # 1. Ingestion
    logger.info("[1/3] ingestion")
    ingest_stock_prices(session=session)
    ingest_commodity_prices(session=session)
    ingest_macros(session=session)
    ingest_india_10y(session=session)
    ingest_parent_prices(session=session)
    
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
    # Policy news (V2): same failure-aware pattern. Google News RSS is
    # generally reliable but RSS endpoints can change. V2 scorer detects
    # stale ingestion via ingestion_runs log.
    try:
        pn_summary = ingest_policy_news(session=session)
        logger.info(
            f"Policy news: ingested={pn_summary['ingested']}, "
            f"classified={pn_summary['classified']}, "
            f"skipped_old={pn_summary['skipped_old']}, "
            f"by_subtype={pn_summary['by_subtype']}"
        )
    except Exception as e:
        logger.exception(f"Policy news ingestion failed: {e}")
        logger.warning(
            "Continuing daily run without policy news refresh. "
            "V2 will report stale-ingestion status until next successful run."
        )    

    # Insider trades (V1): SEBI PIT Reg 7(2) disclosures from NSE.
    # Same failure-aware pattern as V12 corporate actions and V2 policy news.
    # V1 scorer (Session 2) will detect stale ingestion via ingestion_runs log.
    try:
        it_summary = ingest_insider_trades(session=session)
        logger.info(
            f"Insider trades: ingested={it_summary['ingested']}, "
            f"skipped_no_stock={it_summary['skipped_no_stock']}, "
            f"skipped_duplicate={it_summary['skipped_duplicate']}, "
            f"by_category={it_summary['by_category']}"
        )
    except Exception as e:
        logger.exception(f"Insider trades ingestion failed: {e}")
        logger.warning(
            "Continuing daily run without insider trades refresh. "
            "V1 will report stale-ingestion status until next successful run."
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

    v2 = GovtPolicyScorer(session=session)
    v2_results = v2.score_universe(stocks, asof)
    v2.write_scores(v2_results, asof)

    v11 = GlobalParallelsScorer(session=session)
    v11_results = v11.score_universe(stocks, asof)
    v11.write_scores(v11_results, asof)

    v1 = PromotersScorer(session=session)
    v1_results = v1.score_universe(stocks, asof)
    v1.write_scores(v1_results, asof)

    # 3. Confluence
    logger.info("[3/3] confluence")
    compute_confluence(asof, session=session)

    logger.info("=== done ===")
    
    # 4. Optional: push to R2 for production refresh
    if args.push:
        logger.info("--push flag set; uploading DB to R2")
        from scripts.upload_db_to_r2 import main as upload_main
        try:
            upload_main()
        except SystemExit as e:
            if e.code != 0:
                logger.error(
                    f"R2 upload failed with exit code {e.code}. "
                    "Local DB is still updated; production data is stale."
                )
                raise


if __name__ == "__main__":
    main()