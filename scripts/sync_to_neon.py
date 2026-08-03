"""
Sync finished results from local SQLite -> Neon.

Strategy (ADR-004 follow-up): the previous version used ON CONFLICT DO UPDATE for
the score tables. In practice the upsert conflict path silently preserved stale
composite values on already-existing rows, so re-runs didn't update the numbers
that mattered. The fix is to REPLACE each date's score rows: delete-that-date,
then bulk-insert fresh. This is the pattern that worked reliably by hand, and
it's the correct shape anyway — a nightly run replaces a whole day's scores
rather than merging partial updates.

- stocks:            UPSERT on id (merge universe/tier changes; never wipe)
- price_daily:       DELETE date -> INSERT   (that date only)
- vector_scores:     DELETE date -> INSERT   (that date only)
- confluence_scores: DELETE date -> INSERT   (that date only)

Usage:
    python -m scripts.sync_to_neon                 # latest local scored date
    python -m scripts.sync_to_neon 2026-07-29

Requires CONFLUX_DATABASE_URL (Neon OWNER/writer url) in .env.
Idempotent: safe to re-run any number of times for the same date.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date as date_type

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

load_dotenv()

from data.schema import (  # noqa: E402
    Base, Stock, PriceDaily, VectorScore, ConfluenceScore,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("conflux.sync")

CHUNK = 500


def _local_rows(local_session, model, whereclause=None):
    """Read rows from local SQLite keyed by real column names (Core select)."""
    q = select(model.__table__)
    if whereclause is not None:
        q = q.where(whereclause)
    return [dict(r._mapping) for r in local_session.execute(q)]


def _retry(fn, label, tries=3):
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if attempt == tries:
                raise
            wait = 3 * attempt
            logger.warning(f"{label} failed ({e.__class__.__name__}); retry {attempt}/{tries-1} in {wait}s")
            time.sleep(wait)


def _upsert_stocks(pg_engine, rows):
    """Stocks: merge on id so universe/tier edits propagate; never delete."""
    if not rows:
        logger.info("stocks: nothing to sync")
        return
    update_cols = ["symbol_nse", "symbol_yf", "name", "sector", "sub_sector",
                   "market_cap_cr", "in_nifty50", "in_nifty100", "in_nifty500",
                   "promoter_group", "global_parent", "parent_ticker", "tier",
                   "active", "notes"]
    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]

        def _do():
            with pg_engine.begin() as conn:
                stmt = pg_insert(Stock.__table__).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={c: stmt.excluded[c] for c in update_cols},
                )
                conn.execute(stmt)
        _retry(_do, f"stocks chunk {i // CHUNK}")
        total += len(chunk)
    logger.info(f"stocks: upserted {total} rows")


def _replace_date(pg_engine, model, rows, asof, label):
    """DELETE this date's rows, then bulk-insert the fresh set. No conflict path."""
    def _delete():
        with pg_engine.begin() as conn:
            n = conn.execute(
                text(f"DELETE FROM {model.__tablename__} WHERE date = :d"), {"d": asof}
            ).rowcount
            return n
    deleted = _retry(_delete, f"{label} delete")
    logger.info(f"{label}: deleted {deleted} old rows for {asof}")

    if not rows:
        logger.info(f"{label}: no local rows to insert")
        return

    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]

        def _do():
            with pg_engine.begin() as conn:
                conn.execute(model.__table__.insert(), chunk)
        _retry(_do, f"{label} insert chunk {i // CHUNK}")
        total += len(chunk)
    logger.info(f"{label}: inserted {total} fresh rows")


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
    logger.info(f"Syncing results for {asof} -> Neon (replace-by-date)")

    # 1) stocks — upsert (carries universe expansion + tier labels)
    _upsert_stocks(pg_engine, _local_rows(ls, Stock))

    # 2) score tables — delete-then-insert for this date
    _replace_date(pg_engine, PriceDaily,
                  _local_rows(ls, PriceDaily, PriceDaily.date == asof), asof, "price_daily")
    _replace_date(pg_engine, VectorScore,
                  _local_rows(ls, VectorScore, VectorScore.date == asof), asof, "vector_scores")
    _replace_date(pg_engine, ConfluenceScore,
                  _local_rows(ls, ConfluenceScore, ConfluenceScore.date == asof), asof, "confluence_scores")

    # 3) verify parity on the headline table
    with pg_engine.connect() as c:
        neon_n = c.execute(text("SELECT count(*) FROM confluence_scores WHERE date = :d"),
                           {"d": asof}).scalar()
    local_n = ls.execute(select(func.count()).select_from(ConfluenceScore)
                         .where(ConfluenceScore.date == asof)).scalar()
    logger.info(f"verify: confluence_scores local={local_n} neon={neon_n} "
                f"{'OK' if local_n == neon_n else 'MISMATCH!'}")
    logger.info(f"Sync complete for {asof}.")


if __name__ == "__main__":
    main()