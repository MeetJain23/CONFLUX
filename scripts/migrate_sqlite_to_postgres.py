"""
One-shot migration: local data/conflux.db (SQLite) -> Neon Postgres.

    # .env must contain CONFLUX_DATABASE_URL=postgresql+psycopg://...
    python -m scripts.migrate_sqlite_to_postgres

- Creates all tables on Postgres from the same SQLAlchemy models (ADR-001's
  promised escape hatch, now used).
- Copies every table's rows in dependency order, preserving primary keys so
  all foreign keys stay valid.
- Tags every stock that exists in your current SQLite DB as tier=1 — they ARE
  the curated moat. build_universe.py later adds tier-2 stocks around them.
- Idempotent-ish: refuses to run if the Postgres stocks table already has rows
  (protects against double-copy). Drop/recreate the Neon DB to retry.
- Resets Postgres sequences afterward so future inserts don't collide.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

from data import schema  # noqa: E402
from data.schema import Base  # noqa: E402

TABLE_ORDER = [
    "stocks", "commodities", "stock_input_commodities", "stock_output_commodities",
    "stock_customers", "stock_suppliers", "price_daily", "commodity_daily",
    "macro_daily", "vector_scores", "confluence_scores", "ingestion_runs",
    "corporate_actions", "insider_trades", "policy_events", "supply_events",
]


def main():
    pg_url = os.getenv("CONFLUX_DATABASE_URL")
    if not pg_url or not pg_url.startswith("postgresql"):
        sys.exit("Set CONFLUX_DATABASE_URL to your Neon postgresql+psycopg:// URL in .env first.")

    sqlite_engine = create_engine("sqlite:///data/conflux.db", future=True)
    pg_engine = create_engine(pg_url, future=True, pool_pre_ping=True)

    Base.metadata.create_all(pg_engine)
    print("✓ Tables created on Postgres")

    with pg_engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM stocks")).scalar()
        if n:
            sys.exit(f"Postgres already has {n} stocks — refusing to double-copy. "
                     "Recreate the Neon database to re-run.")

    tables = {t.name: t for t in Base.metadata.sorted_tables}
    SqliteSession = sessionmaker(bind=sqlite_engine, future=True)
    src = SqliteSession()

    with pg_engine.begin() as dst:
        for name in TABLE_ORDER:
            t = tables.get(name)
            if t is None:
                continue
            rows = [dict(r._mapping) for r in src.execute(t.select())]
            if rows:
                dst.execute(t.insert(), rows)
            print(f"  {name:28s} {len(rows):>7d} rows")

        dst.execute(text("UPDATE stocks SET tier = 1"))
        print("✓ All migrated stocks tagged tier=1 (the curated moat)")

        # Reset sequences (SERIAL/IDENTITY) to max(id)+1
        for name in TABLE_ORDER:
            t = tables.get(name)
            if t is None or "id" not in t.c:
                continue
            dst.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{name}','id'), "
                f"COALESCE((SELECT MAX(id) FROM {name}), 0) + 1, false)"))
        print("✓ Sequences reset")

    print("\nDone. Verify:  python -m scripts.show_confluence  (with CONFLUX_DATABASE_URL set)")


if __name__ == "__main__":
    main()
