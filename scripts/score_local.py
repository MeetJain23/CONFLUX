"""
Score the FULL expanded universe LOCALLY, against SQLite — no Neon, no drops.

This is the fix for the Neon connection-drop problem. The scorers do one DB
query per stock (thousands of round-trips); that pattern is fine against a
local SQLite file (no network, never disconnects) but fatal against serverless
Postgres that hangs up idle connections mid-loop.

So: score locally here, then run `scripts.sync_to_neon` to push only the
finished results up in a few bulk inserts.

    # 1) force LOCAL sqlite regardless of .env, then score everything:
    python -m scripts.score_local
    python -m scripts.score_local 2026-07-29

Identical scoring logic to run_daily, but:
  - universe = ALL active stocks (tier 1 + tier 2), not just in_nifty500
  - explicitly ignores CONFLUX_DATABASE_URL so it always hits data/conflux.db
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date as date_type

# CRITICAL: unset the Neon URL so get_engine() falls back to local SQLite,
# even if .env is present. This script is deliberately local-only.
os.environ.pop("CONFLUX_DATABASE_URL", None)

from data.schema import Stock, init_db, get_session
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
logger = logging.getLogger("conflux.score_local")

SCORERS = [PromotersScorer, GovtPolicyScorer, InputMaterialCostScorer,
           SupplyDisruptionScorer, GlobalParallelsScorer, RerateCatalystScorer,
           MacroScorer]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asof", nargs="?", default=None)
    args = ap.parse_args()

    init_db()
    session = get_session()  # local sqlite, guaranteed

    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        raise SystemExit("score_local expected SQLite but got "
                         f"{bind.dialect.name}. Aborting to avoid hitting Neon.")

    asof = date_type.fromisoformat(args.asof) if args.asof else date_type.today()
    logger.info(f"=== score_local (SQLite) for {asof} ===")

    stocks = session.query(Stock).filter(Stock.active.is_(True)).all()
    tier1 = sum(1 for s in stocks if getattr(s, "tier", 2) == 1)
    logger.info(f"Universe: {len(stocks)} active ({tier1} tier-1, {len(stocks)-tier1} tier-2)")

    for cls in SCORERS:
        scorer = cls(session=session)
        results = scorer.score_universe(stocks, asof)
        scorer.write_scores(results, asof)
        logger.info(f"V{scorer.vector_id}: scored {len(results)}/{len(stocks)}")

    n = compute_confluence(asof, session=session)
    logger.info(f"=== done (local): {n} composite scores for {asof} ===")
    logger.info("Next: python -m scripts.sync_to_neon  (pushes results to Neon)")


if __name__ == "__main__":
    main()
