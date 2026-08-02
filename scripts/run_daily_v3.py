"""

# SUPERSEDED: scored directly against Neon, which drops connections. Use score_local + sync_to_neon instead


Daily runner v3 — connection-resilient for Neon free tier.

Same pipeline as v2, but hardened against Neon dropping idle connections:

  - INGESTION runs on its own session. If it drops mid-way, we roll back and
    keep going — ingestion failures already degrade vectors honestly.
  - SCORING no longer shares one session across all 7 scorers. Each scorer gets
    a FRESH session via run_scorer_resilient(), which retries the scorer on a
    dropped connection. One vector's drop can no longer cascade into the others
    (that cascade is exactly what killed the v2 run).
  - Writes are BATCHED (100 rows/commit) so a drop costs one chunk, not a vector.
  - CONFLUENCE also runs through the resilient wrapper.

Run:
    python -m scripts.run_daily_v3
    python -m scripts.run_daily_v3 2026-07-29
"""

from __future__ import annotations

import argparse
import logging
from datetime import date as date_type

from dotenv import load_dotenv
load_dotenv()

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

from scripts.db_resilient import run_scorer_resilient, run_confluence_resilient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("conflux.daily.v3")

SCORERS = [PromotersScorer, GovtPolicyScorer, InputMaterialCostScorer,
           SupplyDisruptionScorer, GlobalParallelsScorer, RerateCatalystScorer,
           MacroScorer]


def _safe(label, fn, session, *a, **kw):
    """Ingestion wrapper: on ANY error, roll the ingestion session back so a
    dropped connection here can't poison the next ingestion step."""
    try:
        out = fn(*a, session=session, **kw)
        logger.info(f"{label}: ok")
        return out
    except Exception as e:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:
            pass
        logger.warning(f"{label} failed: {e} — rolled back, continuing")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asof", nargs="?", default=None)
    args = ap.parse_args()

    init_db()

    # ---- resolve date ----
    if args.asof:
        asof = date_type.fromisoformat(args.asof)
    else:
        got = latest_trading_bhavcopy()
        asof = got[0] if got else date_type.today()
    logger.info(f"=== CONFLUX v3 daily run for {asof} ===")

    # ---- 1. INGESTION (own session, isolated) ----
    logger.info("[1/3] ingestion")
    ing = get_session()
    try:
        _safe("bhavcopy (full NSE EOD)", ingest_bhavcopy, ing, asof=asof)
        intl = [s.symbol_yf for s in ing.query(Stock)
                .filter(Stock.active.is_(True), Stock.symbol_nse.like("%\\_INTL")).all()]  # noqa: W605
        if intl:
            _safe(f"international prices ({len(intl)})", ingest_stock_prices, ing, symbols_yf=intl)
        _safe("commodity prices", ingest_commodity_prices, ing)
        _safe("macros", ingest_macros, ing)
        _safe("India 10Y (FRED)", ingest_india_10y, ing)
        _safe("parent prices (V11)", ingest_parent_prices, ing)
        _safe("corporate actions (V12)", ingest_corporate_actions, ing)
        _safe("policy news (V2)", ingest_policy_news, ing)
        _safe("supply news (V6)", ingest_supply_news, ing)
        _safe("insider trades (V1)", ingest_insider_trades, ing)
    finally:
        ing.close()   # release before the long scoring phase so Neon isn't holding an idle conn

    # ---- 2. SCORERS (fresh session each, resilient) ----
    logger.info("[2/3] scorers")
    lookup = get_session()
    stocks = lookup.query(Stock).filter(Stock.active.is_(True)).all()
    lookup.expunge_all()          # detach so objects survive after we close the session
    lookup.close()
    tier1 = sum(1 for s in stocks if getattr(s, "tier", 2) == 1)
    logger.info(f"Universe: {len(stocks)} active ({tier1} tier-1, {len(stocks)-tier1} tier-2)")

    for cls in SCORERS:
        run_scorer_resilient(cls, stocks, asof)

    # ---- 3. CONFLUENCE (resilient) ----
    logger.info("[3/3] confluence")
    n = run_confluence_resilient(compute_confluence, asof)
    logger.info(f"=== done: {n} composite scores for {asof} ===")


if __name__ == "__main__":
    main()