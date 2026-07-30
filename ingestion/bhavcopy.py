"""
NSE bhavcopy ingestion — full-market EOD prices in ONE download.

Why this exists (ADR-003): yfinance loops die at ~2,000 tickers (rate limits,
minutes of wall time, silent gaps). NSE publishes an official end-of-day file
covering every listed equity. One HTTP request ingests the entire universe.

Two formats supported, newest first:
  A) UDiFF bhavcopy (post Jul-2024, ISO 20022 column names):
     https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
  B) Legacy full bhavdata (has DELIV_PER, sometimes lags a day):
     https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

Writes to PriceDaily for every stock already present in the stocks table
(join on symbol_nse). Unknown symbols in the file are ignored — the universe
is controlled by scripts/build_universe.py, not by whatever trades that day.

Also exposes fetch_bhavcopy_frame() so build_universe can reuse the same
download for its penny/liquidity filter.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date as date_type, timedelta

import pandas as pd
from curl_cffi import requests as curl_requests
from sqlalchemy.dialects import postgresql, sqlite

from data.schema import PriceDaily, Stock, IngestionRun, get_session

logger = logging.getLogger(__name__)

ARCHIVES = "https://nsearchives.nseindia.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}

# UDiFF -> canonical column mapping
_UDIFF_COLS = {
    "TckrSymb": "symbol", "SctySrs": "series", "TradDt": "date",
    "OpnPric": "open", "HghPric": "high", "LwPric": "low", "ClsPric": "close",
    "TtlTradgVol": "volume", "TtlTrfVal": "turnover",
}
_LEGACY_COLS = {
    "SYMBOL": "symbol", "SERIES": "series", "DATE1": "date",
    "OPEN_PRICE": "open", "HIGH_PRICE": "high", "LOW_PRICE": "low",
    "CLOSE_PRICE": "close", "TTL_TRD_QNTY": "volume", "TURNOVER_LACS": "turnover_lacs",
}


def _get(url: str, retries: int = 3) -> bytes | None:
    for attempt in range(1, retries + 1):
        try:
            r = curl_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=60)
            if r.status_code == 200 and len(r.content) > 500:
                return r.content
            logger.warning(f"bhavcopy GET {url} -> HTTP {r.status_code} ({len(r.content)} bytes)")
        except Exception as e:  # noqa: BLE001 — network layer, log and retry
            logger.warning(f"bhavcopy GET attempt {attempt} failed: {e}")
        time.sleep(2 * attempt)
    return None


def fetch_bhavcopy_frame(asof: date_type) -> pd.DataFrame | None:
    """
    Returns a normalized DataFrame for the given trading date with columns:
    symbol, series, date, open, high, low, close, volume, turnover_cr
    or None if neither format is available (holiday / not yet published).
    """
    # Format A — UDiFF zip
    ymd = asof.strftime("%Y%m%d")
    content = _get(f"{ARCHIVES}/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip")
    if content:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                name = z.namelist()[0]
                df = pd.read_csv(z.open(name))
            df = df.rename(columns=_UDIFF_COLS)
            keep = ["symbol", "series", "open", "high", "low", "close", "volume", "turnover"]
            df = df[[c for c in keep if c in df.columns]].copy()
            df["date"] = asof
            df["turnover_cr"] = pd.to_numeric(df.get("turnover"), errors="coerce") / 1e7
            df["series"] = df["series"].astype(str).str.strip()
            df["symbol"] = df["symbol"].astype(str).str.strip()
            logger.info(f"bhavcopy(UDiFF) {asof}: {len(df)} rows")
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning(f"UDiFF parse failed for {asof}: {e}")

    # Format B — legacy full bhavdata
    dmy = asof.strftime("%d%m%Y")
    content = _get(f"{ARCHIVES}/products/content/sec_bhavdata_full_{dmy}.csv")
    if content:
        try:
            df = pd.read_csv(io.BytesIO(content))
            df.columns = [c.strip() for c in df.columns]
            df = df.rename(columns=_LEGACY_COLS)
            for col in ("open", "high", "low", "close", "volume", "turnover_lacs"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "").str.strip(),
                                            errors="coerce")
            df["date"] = asof
            df["turnover_cr"] = df.get("turnover_lacs", pd.Series(dtype=float)) / 100.0
            df["series"] = df["series"].astype(str).str.strip()
            df["symbol"] = df["symbol"].astype(str).str.strip()
            logger.info(f"bhavcopy(legacy) {asof}: {len(df)} rows")
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning(f"legacy bhavdata parse failed for {asof}: {e}")

    logger.info(f"No bhavcopy available for {asof} (holiday or not yet published)")
    return None


def latest_trading_bhavcopy(max_lookback_days: int = 7) -> tuple[date_type, pd.DataFrame] | None:
    """Walk backward from today until a bhavcopy exists (skips weekends/holidays)."""
    d = date_type.today()
    for _ in range(max_lookback_days):
        if d.weekday() < 5:
            df = fetch_bhavcopy_frame(d)
            if df is not None and len(df):
                return d, df
        d -= timedelta(days=1)
    return None


def ingest_bhavcopy(asof: date_type | None = None, session=None,
                    series_allowed: tuple[str, ...] = ("EQ", "BE")) -> int:
    """
    Ingest one day's bhavcopy into PriceDaily for all known stocks.
    Bulk upsert — safe to re-run for the same date.
    """
    session = session or get_session()
    run = IngestionRun(job_name="bhavcopy")
    session.add(run)
    session.commit()

    try:
        if asof is None:
            got = latest_trading_bhavcopy()
            if not got:
                raise RuntimeError("no bhavcopy found in lookback window")
            asof, df = got
        else:
            df = fetch_bhavcopy_frame(asof)
            if df is None:
                raise RuntimeError(f"no bhavcopy for {asof}")

        df = df[df["series"].isin(series_allowed)]
        df = df.drop_duplicates(subset=["symbol"], keep="first")

        sym_to_id = {s.symbol_nse: s.id for s in
                     session.query(Stock).filter(Stock.active == True).all()}  # noqa: E712
        rows = []
        for r in df.itertuples(index=False):
            sid = sym_to_id.get(r.symbol)
            if sid is None:
                continue
            rows.append(dict(stock_id=sid, date=asof,
                             open=float(r.open) if pd.notna(r.open) else None,
                             high=float(r.high) if pd.notna(r.high) else None,
                             low=float(r.low) if pd.notna(r.low) else None,
                             close=float(r.close) if pd.notna(r.close) else None,
                             volume=float(r.volume) if pd.notna(r.volume) else None))

        if rows:
            dialect = session.get_bind().dialect.name
            insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
            stmt = insert(PriceDaily.__table__).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "date"],
                set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume")},
            )
            session.execute(stmt)
            session.commit()

        run.status, run.rows_written = "success", len(rows)
        logger.info(f"bhavcopy ingest {asof}: wrote {len(rows)} PriceDaily rows "
                    f"({len(df)} in file, {len(sym_to_id)} stocks in universe)")
        return len(rows)
    except Exception as e:
        session.rollback()
        run.status, run.error_message = "failure", str(e)[:500]
        logger.error(f"bhavcopy ingest failed: {e}")
        return 0
    finally:
        from datetime import datetime as _dt
        run.finished_at = _dt.utcnow()
        session.commit()
