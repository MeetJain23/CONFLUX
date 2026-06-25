"""Quick verify yfinance has data for proposed universe additions."""
import yfinance as yf

new_stocks = [
    "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
    "HCLTECH", "WIPRO", "TECHM", "LTIM", "MPHASIS",
    "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA",
    "DABUR", "BRITANNIA", "MARICO", "GODREJCP", "COLPAL",
    "SBILIFE", "ICICIPRULI", "HDFCLIFE", "SBICARD",
    "NTPC", "TATAPOWER", "ADANIPOWER",
    "BHARTIARTL", "IDEA",
    "HAL", "BEL", "BDL", "MAZDOCK",
    "DLF", "GODREJPROP", "OBEROIRLTY",
    "ADANIGREEN",
    "BOSCHLTD", "MOTHERSON", "DIXON",
]

for symbol in new_stocks:
    ticker = f"{symbol}.NS"
    try:
        info = yf.Ticker(ticker).history(period="5d")
        if len(info) > 0:
            print(f"  OK   {ticker:20} last close: {info['Close'].iloc[-1]:.2f}")
        else:
            print(f"  FAIL {ticker:20} (no data)")
    except Exception as e:
        print(f"  ERR  {ticker:20} {e}")