"""
# SUPERSEDED: scored directly against Neon, which drops connections. Use score_local + sync_to_neon instead



Daily runner v2 — the expanded-universe orchestrator.

    python -m scripts.run_daily_v2               # latest trading day
    python -m scripts.run_daily_v2 2026-07-17    # specific date

Differences from run_daily.py (which stays untouched as the small-universe path):

1. PRICES AT SCALE: NSE stocks are priced via ONE bhavcopy download
   (ingestion/bhavcopy.py) instead of ~2,000 yfinance calls. yfinance is used
   only for the international *_INTL watchlist, commodities, and macros.
2. UNIVERSE: all active stocks (tier 1 + tier 2), not just in_nifty500.
   Scorers self-select coverage — mapped stocks get scores, unmapped return
   None and are honestly absent. Confidence-weighting does the rest.
3. TARGET DB: honors CONFLUX_DATABASE_URL (Neon Postgres) via .env — the same
   run works against local SQLite (unset) or production (set). No R2 push
   needed anymore; the API reads Postgres directly.

Runs the same 7 scorers with the same failure-aware ingestion pattern.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date as date_type

from dotenv import load_dotenv
load_dotenv()  # must run before any get_session() so CONFLUX_DATABASE_URL is seen

from data.schema import Stock, init_db, get_session
from ingestion.bhavcopy import ingest_bhavcopy, latest_trading_bhavcopy
from ingestion.prices import (
    ingest_stock_prices, ingest_commodity_prices, ingest_macros, ingest_india_10y,
)
from ingestion.parent_prices import ingest_parent_prices
from ingestion.corporate_actions import ingest_corporate_actions
from ingestion.policy_news import ingest_policy_news
from ingestion.supply_news import ingest_supply_news
from ingestion.insider_trades import ingest_insider_trades
from confluence.engine import compute_confluence
from scorers.v01_promoters import PromotersScorer
from scorers.v02_govt_policy import GovtPolicyScorer
from scorers.v04_input_material_cost import InputMaterialCostScorer
from scorers.v06_supply_disruption import SupplyDisruptionScorer
from scorers.v11_global_parallels import GlobalParallelsScorer
from scorers.v12_rerating import RerateCatalystScorer
from scorers.v13_macros import MacroScorer

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("conflux.daily.v2")

SCORERS = [PromotersScorer, GovtPolicyScorer, InputMaterialCostScorer,
           SupplyDisruptionScorer, GlobalParallelsScorer, RerateCatalystScorer,
           MacroScorer]


def _safe(label: str, fn, *a, **kw):
    """Failure-aware ingestion: log, continue. Scorers detect staleness via ingestion_runs."""
    try:
        out = fn(*a, **kw)
        logger.info(f"{label}: ok ({out if isinstance(out, (int, dict)) else 'done'})")
        return out
    except Exception as e:  # noqa: BLE001
        logger.exception(f"{label} failed: {e} — continuing; dependent vector degrades honestly")
        return None


def main():
    ap = argparse.ArgumentParser(description="CONFLUX daily refresh (expanded universe)")
    ap.add_argument("asof", nargs="?", default=None, help="ISO date, defaults to latest trading day")
    args = ap.parse_args()

    init_db()
    session = get_session()

    # Resolve the run date to an actual trading day with a bhavcopy
    if args.asof:
        asof = date_type.fromisoformat(args.asof)
    else:
        got = latest_trading_bhavcopy()
        asof = got[0] if got else date_type.today()
    logger.info(f"=== CONFLUX v2 daily run for {asof} ===")

    # ---- 1. Ingestion --------------------------------------------------
    logger.info("[1/3] ingestion")
    _safe("bhavcopy (full NSE EOD)", ingest_bhavcopy, asof=asof, session=session)

    intl = [s.symbol_yf for s in session.query(Stock)
            .filter(Stock.active.is_(True), Stock.symbol_nse.like("%\\_INTL"))  # noqa: W605
            .all()]
    if intl:
        _safe(f"international prices ({len(intl)} via yfinance)",
              ingest_stock_prices, symbols_yf=intl, session=session)

    _safe("commodity prices", ingest_commodity_prices, session=session)
    _safe("macros", ingest_macros, session=session)
    _safe("India 10Y (FRED)", ingest_india_10y, session=session)
    _safe("parent prices (V11)", ingest_parent_prices, session=session)
    _safe("corporate actions (V12)", ingest_corporate_actions, session=session)
    _safe("policy news (V2)", ingest_policy_news, session=session)
    _safe("supply news (V6)", ingest_supply_news, session=session)
    _safe("insider trades (V1)", ingest_insider_trades, session=session)

    # ---- 2. Scorers ----------------------------------------------------
    logger.info("[2/3] scorers")
    stocks = session.query(Stock).filter(Stock.active.is_(True)).all()
    tier1 = sum(1 for s in stocks if getattr(s, "tier", 2) == 1)
    logger.info(f"Universe: {len(stocks)} active ({tier1} tier-1 curated, "
                f"{len(stocks) - tier1} tier-2 automated)")

    for cls in SCORERS:
        scorer = cls(session=session)
        results = scorer.score_universe(stocks, asof)
        scorer.write_scores(results, asof)
        logger.info(f"V{scorer.vector_id}: scored {len(results)}/{len(stocks)} stocks")

    # ---- 3. Confluence -------------------------------------------------
    logger.info("[3/3] confluence")
    n = compute_confluence(asof, session=session)
    logger.info(f"=== done: {n} composite scores for {asof} ===")


if __name__ == "__main__":
    main()
