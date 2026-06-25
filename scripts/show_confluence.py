"""Quick view of today's confluence rankings."""

from datetime import date
from data.schema import Stock, ConfluenceScore, get_session

session = get_session()
asof = date.today()

rows = (
    session.query(ConfluenceScore, Stock)
    .join(Stock, Stock.id == ConfluenceScore.stock_id)
    .filter(ConfluenceScore.date == asof)
    .order_by(ConfluenceScore.composite.desc())
    .all()
)

print(f"{len(rows)} confluence rows for {asof}")
print()
print(f"{'Symbol':<12} {'Sector':<14} {'Composite':>9} {'+V':>3} {'-V':>3} {'Active':>6} {'Direction':<10}")
print("-" * 75)

for c, stk in rows:
    print(
        f"{stk.symbol_nse:<12} {stk.sector:<14} {c.composite:>+9.3f} "
        f"{c.n_vectors_positive:>3} {c.n_vectors_negative:>3} "
        f"{c.n_vectors_active:>6} {c.direction:<10}"
    )