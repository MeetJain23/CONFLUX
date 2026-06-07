"""
Stock price ingestion via yfinance.
Idempotent: re-running fills only missing dates.
"""

from datetime import date as date_type, timedelta
import logging

import yfinance as yf

from data.schema import Stock, PriceDaily, get_session

logger = logging.getLogger(__name__)


def ingest_stock_prices(symbols_yf: list[str] | None = None,
                        lookback_days: int = 400, session=None):
    session = session or get_session()

    stocks = session.query(Stock).filter(Stock.active.is_(True)).all()
    if symbols_yf:
        stocks = [s for s in stocks if s.symbol_yf in symbols_yf]

    end = date_type.today()
    start = end - timedelta(days=lookback_days)

    total_written = 0
    for stock in stocks:
        try:
            df = yf.download(
                stock.symbol_yf,
                start=start.isoformat(), end=end.isoformat(),
                progress=False, auto_adjust=False,
                multi_level_index=False,
            )
            if df is None or df.empty:
                logger.warning(f"No data for {stock.symbol_yf}")
                continue

            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                existing = (
                    session.query(PriceDaily)
                    .filter_by(stock_id=stock.id, date=d).first()
                )
                if existing:
                    continue
                session.add(PriceDaily(
                    stock_id=stock.id, date=d,
                    open=float(row.get("Open", 0) or 0),
                    high=float(row.get("High", 0) or 0),
                    low=float(row.get("Low", 0) or 0),
                    close=float(row.get("Close", 0) or 0),
                    volume=float(row.get("Volume", 0) or 0),
                ))
                total_written += 1
            session.commit()
        except Exception as e:
            logger.exception(f"Failed ingesting {stock.symbol_yf}: {e}")
            session.rollback()

    logger.info(f"Stock prices: wrote {total_written} rows")
    return total_written
