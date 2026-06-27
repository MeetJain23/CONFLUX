"""
Global parent company price ingestion via yfinance.

Stores parent closes in MacroDaily under series_code = f"PARENT_{ticker}".
Reuses MacroDaily (no schema migration) — parent prices are conceptually
external time series, same shape as USDINR / US10Y / INDIA10Y.

Idempotent: re-running fills only missing dates.
"""

from datetime import date as date_type, timedelta
import logging

import yfinance as yf

from data.schema import Stock, MacroDaily, get_session

logger = logging.getLogger(__name__)


def ingest_parent_prices(lookback_days: int = 400, session=None):
    session = session or get_session()

    rows = (
        session.query(Stock.parent_ticker)
        .filter(Stock.active.is_(True))
        .filter(Stock.parent_ticker.isnot(None))
        .filter(Stock.parent_ticker != "")
        .distinct()
        .all()
    )
    parent_tickers = sorted({r.parent_ticker for r in rows})

    if not parent_tickers:
        logger.info("Parent prices: no parent_ticker mappings, skipping")
        return 0

    end = date_type.today()
    start = end - timedelta(days=lookback_days)
    total_written = 0

    for ticker in parent_tickers:
        series_code = f"PARENT_{ticker}"
        try:
            df = yf.download(
                ticker,
                start=start.isoformat(), end=end.isoformat(),
                progress=False, auto_adjust=False,
                multi_level_index=False,
            )
            if df is None or df.empty:
                logger.warning(f"No data for parent ticker {ticker}")
                continue

            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                close = float(row.get("Close", 0) or 0)
                if close == 0:
                    continue
                existing = (
                    session.query(MacroDaily)
                    .filter_by(series_code=series_code, date=d)
                    .first()
                )
                if existing:
                    continue
                session.add(MacroDaily(
                    series_code=series_code,
                    date=d,
                    value=close,
                    source="yfinance",
                ))
                total_written += 1
            session.commit()
        except Exception as e:
            logger.exception(f"Failed ingesting parent {ticker}: {e}")
            session.rollback()

    logger.info(
        f"Parent prices: wrote {total_written} rows across {len(parent_tickers)} parents"
    )
    return total_written