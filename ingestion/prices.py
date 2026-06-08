"""
Stock and commodity price ingestion via yfinance.

Phase 1 strategy:
- yfinance for stock daily OHLCV (free, no API key)
- yfinance for commodity proxies (BZ=F for Brent, CL=F for WTI, HG=F for copper, etc.)
- Macros from yfinance/FRED-style proxies (INR=X for USDINR, ^TNX for US10Y, etc.)

Each ingester is idempotent: re-running fills only missing dates.
"""

from datetime import date as date_type, timedelta
import logging

import yfinance as yf

from data.schema import (
    Stock, Commodity, PriceDaily, CommodityDaily, MacroDaily, get_session,
)

logger = logging.getLogger(__name__)


def ingest_stock_prices(symbols_yf: list[str] | None = None, lookback_days: int = 400, session=None):
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
                start=start.isoformat(),
                end=end.isoformat(),
                progress=False,
                auto_adjust=False,
                multi_level_index=False,
            )
            if df is None or df.empty:
                logger.warning(f"No data for {stock.symbol_yf}")
                continue

            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                existing = (
                    session.query(PriceDaily)
                    .filter_by(stock_id=stock.id, date=d)
                    .first()
                )
                if existing:
                    continue
                session.add(
                    PriceDaily(
                        stock_id=stock.id, date=d,
                        open=float(row.get("Open", 0) or 0),
                        high=float(row.get("High", 0) or 0),
                        low=float(row.get("Low", 0) or 0),
                        close=float(row.get("Close", 0) or 0),
                        volume=float(row.get("Volume", 0) or 0),
                    )
                )
                total_written += 1
            session.commit()
        except Exception as e:
            logger.exception(f"Failed ingesting {stock.symbol_yf}: {e}")
            session.rollback()

    logger.info(f"Stock prices: wrote {total_written} rows")
    return total_written


def ingest_commodity_prices(lookback_days: int = 400, session=None):
    session = session or get_session()
    commodities = session.query(Commodity).filter(Commodity.active.is_(True)).all()

    end = date_type.today()
    start = end - timedelta(days=lookback_days)
    total = 0
    for c in commodities:
        if not c.yf_ticker:
            continue
        try:
            df = yf.download(c.yf_ticker, start=start.isoformat(), end=end.isoformat(),
                             progress=False, 
                             auto_adjust=False,
                             multi_level_index=False,
                             )
            if df is None or df.empty:
                continue
            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                existing = (
                    session.query(CommodityDaily)
                    .filter_by(commodity_id=c.id, date=d).first()
                )
                if existing:
                    continue
                session.add(
                    CommodityDaily(
                        commodity_id=c.id, date=d,
                        close=float(row.get("Close", 0) or 0),
                        source="yfinance",
                    )
                )
                total += 1
            session.commit()
        except Exception as e:
            logger.exception(f"Failed commodity {c.code}: {e}")
            session.rollback()
    logger.info(f"Commodity prices: wrote {total} rows")
    return total


# Macro series mapping → yfinance ticker (phase 1)
MACRO_TICKERS = {
    "USDINR":   "INR=X",
    "US10Y":    "^TNX",
    # INDIA10Y: ^INRX delisted on yfinance. TODO: replace with FRED IRLTLT01INM156N in Phase 2.
    "BRENT":    "BZ=F",
    "DXY":      "DX-Y.NYB",
}


def ingest_macros(lookback_days: int = 400, session=None):
    session = session or get_session()
    end = date_type.today()
    start = end - timedelta(days=lookback_days)
    total = 0
    for series_code, ticker in MACRO_TICKERS.items():
        try:
            df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                             progress=False,
                             auto_adjust=False, 
                             multi_level_index=False,
                             )
            if df is None or df.empty:
                continue
            for ts, row in df.iterrows():
                d = ts.date() if hasattr(ts, "date") else ts
                existing = (
                    session.query(MacroDaily)
                    .filter_by(series_code=series_code, date=d).first()
                )
                if existing:
                    continue
                session.add(
                    MacroDaily(
                        series_code=series_code, date=d,
                        value=float(row.get("Close", 0) or 0),
                        source="yfinance",
                    )
                )
                total += 1
            session.commit()
        except Exception as e:
            logger.exception(f"Failed macro {series_code}: {e}")
            session.rollback()
    logger.info(f"Macros: wrote {total} rows")
    return total
