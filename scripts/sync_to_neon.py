"""
Push finished results from local SQLite -> Neon, in bulk.

Why this works where per-stock scoring failed: instead of thousands of tiny
round-trips (which let Neon idle-drop mid-loop), this does a handful of large
chunked upserts. Each chunk is one network operation; Neon has no idle window
to hang up in, and if a chunk does fail, we retry that one chunk, not a whole
scorer.

Syncs, for a given date (default = latest scored date in local DB):
  - stocks            (full table upsert — picks up the expanded universe & tiers)
  - price_daily       (that date only)
  - vector_scores     (that date only)
  - confluence_scores (that date only)

Usage:
    python -m scripts.sync_to_neon                 # latest local date
    python -m scripts.sync_to_neon 2026-07-29

Requires CONFLUX_DATABASE_URL (Neon, the OWNER/writer url) in .env.
Safe to re-run: everything is upserted on natural keys.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date as date_type

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

load_dotenv()

from data.schema import (  # noqa: E402
    Base, Stock, PriceDaily, VectorScore, ConfluenceScore,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("conflux.sync")

CHUNK = 500


def _rows(session, table, whereclause=None):
    q = select(table)
    if whereclause is not None:
        q = q.where(whereclause)
    return [dict(r._mapping) for r in session.execute(q)]


def _bulk_upsert(pg_engine, table, rows, conflict_cols, update_cols, label):
    if not rows:
        logger.info(f"{label}: nothing to sync")
        return
    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        for attempt in range(1, 4):
            try:
                with pg_engine.begin() as conn:
                    stmt = pg_insert(table).values(chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=conflict_cols,
                        set_={c: stmt.excluded[c] for c in update_cols},
                    )
                    conn.execute(stmt)
                total += len(chunk)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    raise
                wait = 3 * attempt
                logger.warning(f"{label} chunk {i//CHUNK} failed ({e.__class__.__name__}); "
                               f"retry {attempt}/2 in {wait}s")
                time.sleep(wait)
    logger.info(f"{label}: synced {total} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asof", nargs="?", default=None)
    args = ap.parse_args()

    pg_url = os.getenv("CONFLUX_DATABASE_URL")
    if not pg_url or not pg_url.startswith("postgresql"):
        raise SystemExit("Set CONFLUX_DATABASE_URL (Neon owner url) in .env first.")

    sqlite_engine = create_engine("sqlite:///data/conflux.db", future=True)
    pg_engine = create_engine(pg_url, future=True, pool_pre_ping=True)
    Base.metadata.create_all(pg_engine)  # no-op if tables exist

    Local = sessionmaker(bind=sqlite_engine, future=True)
    ls = Local()

    asof = (date_type.fromisoformat(args.asof) if args.asof
            else ls.execute(select(func.max(ConfluenceScore.date))).scalar())
    if asof is None:
        raise SystemExit("No confluence scores in local DB — run score_local first.")
    logger.info(f"Syncing results for {asof} -> Neon")

    # 1) stocks (whole table — carries universe expansion + tiers)
    _bulk_upsert(
        pg_engine, Stock.__table__, _rows(ls, Stock.__table__),
        conflict_cols=["id"],
        update_cols=["symbol_nse", "symbol_yf", "name", "sector", "sub_sector",
                     "market_cap_cr", "in_nifty50", "in_nifty100", "in_nifty500",
                     "promoter_group", "global_parent", "parent_ticker", "tier",
                     "active", "notes"],
        label="stocks",
    )

    # 2) price_daily for asof
    _bulk_upsert(
        pg_engine, PriceDaily.__table__,
        _rows(ls, PriceDaily.__table__, PriceDaily.date == asof),
        conflict_cols=["stock_id", "date"],
        update_cols=["open", "high", "low", "close", "volume"],
        label="price_daily",
    )

    # 3) vector_scores for asof
    _bulk_upsert(
        pg_engine, VectorScore.__table__,
        _rows(ls, VectorScore.__table__, VectorScore.date == asof),
        conflict_cols=["stock_id", "vector_id", "date"],
        update_cols=["score", "confidence", "rationale", "components_json"],
        label="vector_scores",
    )

    # 4) confluence_scores for asof
    _bulk_upsert(
        pg_engine, ConfluenceScore.__table__,
        _rows(ls, ConfluenceScore.__table__, ConfluenceScore.date == asof),
        conflict_cols=["stock_id", "date"],
        update_cols=["composite", "n_vectors_positive", "n_vectors_negative",
                     "n_vectors_active", "direction", "vector_breakdown_json"],
        label="confluence_scores",
    )

    logger.info(f"Sync complete for {asof}. Neon now holds the full published results.")


if __name__ == "__main__":
    main()
