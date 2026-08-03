"""One-shot: clear stale 2026-07-29 confluence rows on Neon, verify empty,
re-insert from local, verify match. Bypasses the upsert conflict path entirely."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
load_dotenv()

DATE = "2026-07-29"
neon_url = os.getenv("CONFLUX_DATABASE_URL")
neon = create_engine(neon_url)
local = create_engine("sqlite:///data/conflux.db")

# 1. delete stale rows on Neon
with neon.begin() as c:
    n = c.execute(text("DELETE FROM confluence_scores WHERE date = :d"), {"d": DATE}).rowcount
    print(f"deleted {n} stale rows from Neon")

# 2. confirm empty
with neon.connect() as c:
    left = c.execute(text("SELECT count(*) FROM confluence_scores WHERE date = :d"), {"d": DATE}).scalar()
    print(f"Neon rows for {DATE} after delete: {left}")

# 3. read correct rows from local
with local.connect() as c:
    rows = [dict(r._mapping) for r in c.execute(
        text("SELECT * FROM confluence_scores WHERE date = :d"), {"d": DATE})]
    print(f"local rows to copy: {len(rows)}")

# 4. bulk insert into Neon (fresh inserts, no conflict path)
from data.schema import ConfluenceScore
with neon.begin() as c:
    for i in range(0, len(rows), 500):
        c.execute(ConfluenceScore.__table__.insert(), rows[i:i+500])
print("inserted into Neon")

# 5. verify both sides agree
q = ("SELECT s.symbol_nse, c.composite FROM confluence_scores c "
     "JOIN stocks s ON s.id=c.stock_id WHERE c.date=:d ORDER BY c.composite DESC LIMIT 3")
print("LOCAL:")
with local.connect() as c:
    for r in c.execute(text(q), {"d": DATE}): print("  ", r[0], round(r[1], 3))
print("NEON:")
with neon.connect() as c:
    for r in c.execute(text(q), {"d": DATE}): print("  ", r[0], round(r[1], 3))