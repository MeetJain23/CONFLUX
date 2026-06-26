"""Pull INDIGO's recent price history + recent confluence scores to verify the move story."""
from datetime import date, timedelta
from data.schema import Stock, PriceDaily, ConfluenceScore, get_session

session = get_session()
stock = session.query(Stock).filter_by(symbol_nse="INDIGO").first()

print(f"=== INDIGO recent prices (last 10 days) ===")
prices = (
    session.query(PriceDaily)
    .filter_by(stock_id=stock.id)
    .filter(PriceDaily.date >= date.today() - timedelta(days=10))
    .order_by(PriceDaily.date.asc())
    .all()
)
for p in prices:
    print(f"  {p.date}  close: {p.close:>8.2f}")

print()
print(f"=== INDIGO recent CONFLUX composites (last 10 days) ===")
scores = (
    session.query(ConfluenceScore)
    .filter_by(stock_id=stock.id)
    .filter(ConfluenceScore.date >= date.today() - timedelta(days=10))
    .order_by(ConfluenceScore.date.asc())
    .all()
)
for s in scores:
    print(f"  {s.date}  composite: {s.composite:>+.3f}  direction: {s.direction}")