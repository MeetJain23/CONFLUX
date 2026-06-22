"""Inspect V2 contributions for a single stock."""
import sys
from datetime import date
from data.schema import Stock, get_session
from scorers.v02_govt_policy import GovtPolicyScorer

symbol = sys.argv[1] if len(sys.argv) > 1 else "PIDILITIND"

session = get_session()
stock = session.query(Stock).filter_by(symbol_nse=symbol).first()
if stock is None:
    print(f"Stock {symbol} not found")
    sys.exit(1)

scorer = GovtPolicyScorer(session=session)
result = scorer.score_one(stock, date.today())

if result is None:
    print(f"{symbol}: No V2 signal")
else:
    print(f"=== V2 BREAKDOWN: {symbol} ===")
    print(f"Score: {result.score:+.4f}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Rationale: {result.rationale}")
    print()
    print("Components:")
    import json
    print(json.dumps(result.components, indent=2, default=str))