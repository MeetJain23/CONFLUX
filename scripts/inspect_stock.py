"""Inspect all vector scores for a single stock."""
import sys
from datetime import date
from data.schema import Stock, VectorScore, get_session

symbol = sys.argv[1] if len(sys.argv) > 1 else "BOSCHLTD"

session = get_session()
stock = session.query(Stock).filter_by(symbol_nse=symbol).first()
if not stock:
    print(f"Stock {symbol} not found")
    sys.exit(1)

scores = (
    session.query(VectorScore)
    .filter_by(stock_id=stock.id, date=date.today())
    .all()
)

print(f"=== {symbol} ({stock.sector}/{stock.sub_sector}) on {date.today()} ===")
for s in scores:
    print(f"  V{s.vector_id} score={s.score:+.4f} conf={s.confidence:.2f}")
    print(f"     {s.rationale[:200]}")