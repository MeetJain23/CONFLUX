"""
SEBI Reg 7(2) PIT (Prohibition of Insider Trading) disclosure ingestion.

Pulls insider trade filings from NSE's /api/corporates-pit endpoint,
normalizes the fields V1 scorer cares about, and persists to the
insider_trades table.

Design notes:
- Source endpoint: /api/corporates-pit?index=equities&from_date=...&to_date=...
  Requires from_date/to_date in DD-MM-YYYY format (NSE rejects shorter
  windows; 30d returns 0 rows, 90d works reliably).
- Most filings (~85%+) are for stocks not in CONFLUX's 86-stock universe.
  These are silently skipped at DEBUG level to avoid log spam — same
  pattern as corporate_actions ingester.
- Idempotency via unique (stock_id, pit_id). NSE's 'pid' field is a
  stable per-filing identifier; re-running on overlapping windows is
  safe (IntegrityError caught, treated as already-ingested).
- We deliberately DO NOT filter routine vs opportunistic at ingest time.
  ESOP, Inheritance, Gift records are stored as-is. The V1 scorer weights
  them down at scoring time — keeping raw data preserves the option to
  re-score later with different weights.

V1 scorer (Session 2) will read from this table:
  - Weight by person_category: Promoter > Promoter Group > Director > KMP
  - Weight by acq_mode: Market Purchase = full, ESOP/Inheritance = ~0
  - Magnitude from pct_change in holding + securities_value
  - Cluster bonus: 3+ insiders buying same stock in 30d = signal boost
  - Direction: transaction_type 'Buy' = positive, 'Sell' = negative

Phase 2 TODOs:
- SAST Reg 29 disclosures (substantial acquisitions, 5%+ holdings).
  Different NSE endpoint, similar shape.
- Reg 31 pledge data (encumbrance disclosures).
- Board changes / KMP resignations via corporate announcements feed.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError

from data.schema import InsiderTrade, IngestionRun, Stock, get_session
from ingestion.nse_session import NSESession, NSESessionError

logger = logging.getLogger(__name__)


# --- Configuration ---------------------------------------------------------

PIT_URL_BASE = "https://www.nseindia.com/api/corporates-pit?index=equities"

# NSE rejects short windows. 90d returns reliably; we paginate by 90d
# chunks if a longer window is requested.
MAX_WINDOW_DAYS = 90


# --- Field parsers ---------------------------------------------------------

NSE_DATE_FORMATS = ["%d-%b-%Y", "%d-%m-%Y", "%d-%b-%Y %H:%M"]


def _parse_date(s: Optional[str]) -> Optional[date]:
    """Parse NSE date strings. Multiple formats observed in PIT payloads."""
    if not s or s in ("-", "NA", ""):
        return None
    s = s.strip()
    # Strip time component if present
    s_date_only = s.split(" ")[0] if " " in s else s
    for fmt in NSE_DATE_FORMATS:
        try:
            return datetime.strptime(s_date_only, fmt).date()
        except ValueError:
            continue
    logger.warning(f"Could not parse PIT date: {s!r}")
    return None


def _parse_float(s) -> Optional[float]:
    """Parse NSE numeric strings. They come as strings, sometimes with commas."""
    if s is None or s in ("-", "NA", ""):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def select_transaction_date(raw: dict) -> Optional[date]:
    """
    Pick the transaction date. Priority:
      1. acqtoDt (transaction window end — most accurate)
      2. acqfromDt (window start, in case end is missing)
      3. intimDt (filing date, fallback only)
    """
    for key in ("acqtoDt", "acqfromDt", "intimDt"):
        d = _parse_date(raw.get(key))
        if d:
            return d
    return None


# --- Main ingester --------------------------------------------------------

def ingest_insider_trades(from_date: Optional[date] = None,
                          to_date: Optional[date] = None,
                          session=None,
                          verbose: bool = False) -> dict:
    """
    Ingest PIT disclosures into the insider_trades table.

    Args:
        from_date: window start (Python date). Default: 90 days ago.
        to_date:   window end. Default: today.
        session:   optional SQLAlchemy session.
        verbose:   if True, log each ingested filing.

    Returns summary dict:
        {
            "status": "success" | "failure",
            "fetched": <int>,                 # raw filings from NSE
            "ingested": <int>,                # written to DB
            "skipped_no_stock": <int>,        # symbol not in universe
            "skipped_duplicate": <int>,       # pit_id already in DB
            "skipped_no_date": <int>,         # missing/unparseable date
            "by_category": {category: count},
            "by_transaction_type": {type: count},
            "by_acq_mode": {mode: count},
            "error": <str | None>,
        }

    Always writes an IngestionRun row for healthcheck visibility.
    """
    session = session or get_session()

    if to_date is None:
        to_date = date.today()
    if from_date is None:
        from_date = to_date - timedelta(days=MAX_WINDOW_DAYS)

    summary = {
        "status": "failure",
        "fetched": 0,
        "ingested": 0,
        "skipped_no_stock": 0,
        "skipped_duplicate": 0,
        "skipped_no_date": 0,
        "by_category": {},
        "by_transaction_type": {},
        "by_acq_mode": {},
        "error": None,
    }

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # --- Fetch from NSE (paginate if window > MAX_WINDOW_DAYS) ---
    all_filings = []
    try:
        with NSESession() as nse:
            window_start = from_date
            while window_start <= to_date:
                window_end = min(window_start + timedelta(days=MAX_WINDOW_DAYS - 1), to_date)
                url = (
                    f"{PIT_URL_BASE}"
                    f"&from_date={window_start.strftime('%d-%m-%Y')}"
                    f"&to_date={window_end.strftime('%d-%m-%Y')}"
                )
                logger.info(
                    f"Fetching PIT filings (from={window_start}, to={window_end})"
                )
                data = nse.fetch_json(url)

                if isinstance(data, list):
                    chunk = data
                elif isinstance(data, dict):
                    chunk = data.get("data", data.get("rows", []))
                else:
                    chunk = []

                logger.info(f"  fetched {len(chunk)} filings for this window")
                all_filings.extend(chunk)
                window_start = window_end + timedelta(days=1)
    except NSESessionError as e:
        summary["error"] = str(e)
        logger.exception(f"PIT fetch failed: {e}")
        _write_ingestion_run(session, "insider_trades", started_at, summary)
        return summary

    summary["fetched"] = len(all_filings)

    # --- Build symbol lookup ---
    stocks_by_symbol = {
        s.symbol_nse: s for s in session.query(Stock).filter(Stock.active.is_(True))
    }

    # --- Process filings ---
    for raw in all_filings:
        symbol = raw.get("symbol")
        if not symbol:
            continue

        stock = stocks_by_symbol.get(symbol)
        if not stock:
            summary["skipped_no_stock"] += 1
            logger.debug(f"Skip {symbol}: not in CONFLUX universe")
            continue

        pit_id = raw.get("pid")
        if not pit_id:
            logger.warning(f"PIT record missing pid for {symbol}: {raw!r}")
            continue

        txn_date = select_transaction_date(raw)
        if not txn_date:
            summary["skipped_no_date"] += 1
            logger.warning(f"Could not extract transaction date for {symbol} / pid={pit_id}")
            continue

        transaction_type = raw.get("tdpTransactionType") or ""
        person_category = raw.get("personCategory") or ""
        acq_mode = raw.get("acqMode") or ""

        trade = InsiderTrade(
            stock_id=stock.id,
            pit_id=str(pit_id),
            person_name=raw.get("acqName") or "",
            person_category=person_category,
            acq_mode=acq_mode,
            transaction_type=transaction_type,
            securities_qty=_parse_float(raw.get("secAcq")),
            securities_value=_parse_float(raw.get("secVal")),
            pct_before=_parse_float(raw.get("befAcqSharesPer")),
            pct_after=_parse_float(raw.get("afterAcqSharesPer")),
            transaction_date=txn_date,
            intimation_date=_parse_date(raw.get("intimDt")),
            regulation=raw.get("anex") or "",
            exchange="NSE",
            raw_payload=raw,
            source_url=PIT_URL_BASE,
            xbrl_url=raw.get("xbrl") or "",
            notes=(raw.get("remarks") or "")[:500],
        )

        try:
            session.add(trade)
            session.commit()
            summary["ingested"] += 1
            summary["by_category"][person_category] = (
                summary["by_category"].get(person_category, 0) + 1
            )
            summary["by_transaction_type"][transaction_type] = (
                summary["by_transaction_type"].get(transaction_type, 0) + 1
            )
            summary["by_acq_mode"][acq_mode] = (
                summary["by_acq_mode"].get(acq_mode, 0) + 1
            )
            if verbose:
                logger.info(
                    f"Ingested PIT: {symbol} {person_category} "
                    f"{transaction_type} {acq_mode} on {txn_date}"
                )
        except IntegrityError:
            session.rollback()
            summary["skipped_duplicate"] += 1
            logger.debug(f"Already ingested: pit_id={pit_id} for {symbol}")

    if summary["error"] is None:
        summary["status"] = "success"

    logger.info(
        f"Insider trades ingest: fetched={summary['fetched']}, "
        f"ingested={summary['ingested']}, "
        f"skipped_no_stock={summary['skipped_no_stock']}, "
        f"skipped_duplicate={summary['skipped_duplicate']}, "
        f"by_category={summary['by_category']}"
    )

    _write_ingestion_run(session, "insider_trades", started_at, summary)
    return summary


def _write_ingestion_run(session, source: str, started_at: datetime, summary: dict):
    """Write a row to ingestion_runs for healthcheck visibility."""
    try:
        run = IngestionRun(
            job_name=source,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status=summary["status"],
            rows_written=summary["ingested"],
            error_message=summary.get("error"),
        )
        session.add(run)
        session.commit()
    except Exception as e:
        logger.exception(f"Failed to write IngestionRun: {e}")
        session.rollback()


# --- Module test entry point ----------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    summary = ingest_insider_trades(verbose=False)
    print("\n=== Ingestion Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")