"""
Corporate Actions ingestion for V12 (Re-rating Catalysts).

Pulls events from NSE via ingestion.nse_session, classifies the free-text
`subject` field into our controlled action_type vocabulary, and upserts
into the corporate_actions table.

Design notes:
- Classification is regex-based, priority-ordered (first match wins).
  See ACTION_TYPE_PATTERNS for the rules and rationale.
- Most NSE events (~90%+) are for stocks not in CONFLUX's universe.
  These are silently skipped (DEBUG-level log, not WARNING) to avoid
  log spam.
- Idempotency via unique constraint on (stock_id, action_type, action_date).
  Re-running on overlapping windows is safe — IntegrityError is caught
  and treated as "already ingested."
- Routine dividends are deliberately dropped at classification time
  (NOT stored as OTHER). Storing every quarterly dividend would bloat
  the table without benefit. Special/interim dividends are stored.

Phase 2 TODOs:
- Yield-based tiering for dividends (split SPECIAL_DIVIDEND into full
  and half magnitude based on yield > 2% threshold). Currently all
  special/interim dividends get full magnitude.
- Board change detection (not in NSE corporate-actions feed; needs
  separate source like BSE announcements or news scraping).
- Promoter pledging deltas (NSDL data; separate ingester).
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError

from data.schema import CorporateAction, IngestionRun, Stock, get_session
from ingestion.nse_session import fetch_corporate_actions as nse_fetch
from ingestion.nse_session import NSESessionError

logger = logging.getLogger(__name__)


# --- Classification rules --------------------------------------------------
#
# Priority-ordered. First pattern that matches the lowercase subject wins.
# Rationale for each pattern is inline. When adding new patterns, place
# them by specificity (most specific first) to avoid the "dividend" rule
# eating "special dividend" etc.

ACTION_TYPE_PATTERNS = [
    # Most specific dividend patterns FIRST so they win against generic "dividend"
    (re.compile(r"\b(special\s+dividend|interim\s+dividend)\b"), "SPECIAL_DIVIDEND"),
    
    # Buyback — common phrasings: "Buy Back of Shares", "Buyback", "Buy-back"
    (re.compile(r"\bbuy[\s\-]?back\b"), "BUYBACK"),
    
    # Bonus issues — "Bonus 1:1", "Bonus Issue", etc.
    (re.compile(r"\bbonus\b"), "BONUS"),
    
    # Stock splits — "Sub-Division", "Sub Division", "Stock Split", "Face Value"
    # change patterns. "Sub-division" is NSE's term, "Stock Split" appears too.
    (re.compile(r"\b(sub[\s\-]?division|stock\s+split|face\s+value\s+split)\b"), "SPLIT"),
    
    # Demerger / Scheme of Arrangement (often used for demergers and mergers)
    # We classify scheme of arrangement as DEMERGER because that's the
    # most common interpretation. Could be refined later.
    (re.compile(r"\b(demerger|scheme\s+of\s+arrangement|spin[\s\-]?off)\b"), "DEMERGER"),
    
    # Rights issues — log as OTHER (potential re-rating signal but
    # different mechanism; not in Phase 1 taxonomy)
    (re.compile(r"\brights\s+issue\b"), "OTHER"),
    
    # Open offers — M&A signal; log as OTHER for now
    (re.compile(r"\bopen\s+offer\b"), "OTHER"),
    
    # Generic dividend WITHOUT "special" or "interim" prefix → routine
    # We explicitly return None for these so they're dropped, not stored.
    # The placeholder type "ROUTINE_DIVIDEND" is a sentinel; the classifier
    # function below converts it to None.
    (re.compile(r"\bdividend\b"), "ROUTINE_DIVIDEND"),
]


def classify_action(subject: Optional[str]) -> Optional[str]:
    """
    Map a free-text NSE subject string to our controlled vocabulary.
    Returns None for events we deliberately want to skip (routine dividends,
    AGMs, meetings, anything unrecognized).
    
    >>> classify_action("Buy Back of Equity Shares")
    'BUYBACK'
    >>> classify_action("Dividend - Rs 0.60 Per Share")  # routine, skip
    >>> classify_action("Special Dividend - Rs 50.00 Per Share")
    'SPECIAL_DIVIDEND'
    >>> classify_action("Annual General Meeting")
    """
    if not subject:
        return None
    
    text = subject.lower()
    
    for pattern, action_type in ACTION_TYPE_PATTERNS:
        if pattern.search(text):
            # Sentinel: explicit "this is a routine dividend, drop it"
            if action_type == "ROUTINE_DIVIDEND":
                return None
            return action_type
    
    # Anything not matching any pattern (AGMs, EGMs, listing notes, etc.)
    # is dropped. We do NOT store as OTHER by default — only events that
    # matched a pattern but we don't have a specific type for (Rights,
    # Open Offer) become OTHER.
    return None


# --- Date parsing ---------------------------------------------------------

NSE_DATE_FORMATS = ["%d-%b-%Y", "%d-%m-%Y"]


def _parse_nse_date(s: Optional[str]) -> Optional[date]:
    """Parse NSE date strings. NSE uses 'DD-Mon-YYYY' (e.g., '15-Jun-2026')."""
    if not s or s == "-" or s.strip() == "":
        return None
    s = s.strip()
    for fmt in NSE_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    logger.warning(f"Could not parse NSE date: {s!r}")
    return None


def select_action_date(raw: dict) -> Optional[date]:
    """
    Pick the most appropriate date field from an NSE event.
    
    Priority:
      1. caBroadcastDate (when announcement was made — best signal for
         "when did the market learn about this?")
      2. exDate (when event takes effect mechanically)
      3. recDate (record date — fallback)
    
    Re-rating fires on announcement, not on execution, so caBroadcastDate
    is the most accurate timestamp when available.
    """
    for key in ("caBroadcastDate", "exDate", "recDate"):
        d = _parse_nse_date(raw.get(key))
        if d:
            return d
    return None


# --- Main ingester --------------------------------------------------------

def ingest_corporate_actions(from_date: Optional[date] = None,
                              to_date: Optional[date] = None,
                              session=None,
                              verbose: bool = False) -> dict:
    """
    Ingest NSE corporate actions into the corporate_actions table.
    
    Args:
        from_date: start of window (Python date). Default: 90 days ago.
        to_date:   end of window. Default: today.
        session:   optional SQLAlchemy session.
        verbose:   if True, log each ingested event individually.
    
    Returns a summary dict:
        {
            "status": "success" | "partial" | "failure",
            "fetched": <int>,            # raw events from NSE
            "classified": <int>,         # events matching a known pattern
            "ingested": <int>,           # events written to DB
            "skipped_no_stock": <int>,   # events for stocks not in our DB
            "skipped_routine": <int>,    # routine dividends dropped
            "skipped_duplicate": <int>,  # already in DB (idempotency)
            "by_type": {action_type: count},
            "error": <str | None>,
        }
    
    Always writes an IngestionRun row regardless of outcome.
    """
    session = session or get_session()
    
    if to_date is None:
        to_date = date.today()
    if from_date is None:
        from_date = to_date - timedelta(days=90)
    
    summary = {
        "status": "failure",
        "fetched": 0,
        "classified": 0,
        "ingested": 0,
        "skipped_no_stock": 0,
        "skipped_routine": 0,
        "skipped_duplicate": 0,
        "by_type": {},
        "error": None,
    }
    
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    # --- Fetch from NSE ---
    try:
        actions_raw = nse_fetch(
            from_date=from_date.strftime("%d-%m-%Y"),
            to_date=to_date.strftime("%d-%m-%Y"),
        )
    except NSESessionError as e:
        summary["error"] = str(e)
        logger.exception(f"NSE fetch failed: {e}")
        _write_ingestion_run(session, "corporate_actions", started_at, summary)
        return summary
    
    summary["fetched"] = len(actions_raw)
    
    # --- Build symbol lookup for our universe ---
    stocks_by_symbol = {
        s.symbol_nse: s for s in session.query(Stock).filter(Stock.active.is_(True))
    }
    
    # --- Classify and ingest ---
    for raw in actions_raw:
        symbol = raw.get("symbol")
        subject = raw.get("subject", "")
        
        if not symbol:
            continue
        
        action_type = classify_action(subject)
        
        if action_type is None:
            # Routine dividend or unrecognized event
            if subject and "dividend" in subject.lower():
                summary["skipped_routine"] += 1
            continue
        
        summary["classified"] += 1
        
        # Stock filter
        stock = stocks_by_symbol.get(symbol)
        if not stock:
            summary["skipped_no_stock"] += 1
            logger.debug(f"Skip {symbol}: not in CONFLUX universe")
            continue
        
        action_date = select_action_date(raw)
        if not action_date:
            logger.warning(f"Could not extract date for {symbol} / {subject!r}")
            continue
        
        ex_date = _parse_nse_date(raw.get("exDate"))
        
        # Upsert via insert + catch IntegrityError
        ca = CorporateAction(
            stock_id=stock.id,
            exchange="NSE",
            action_type=action_type,
            action_date=action_date,
            ex_date=ex_date,
            raw_payload=raw,
            source_url="https://www.nseindia.com/api/corporates-corporateActions",
            notes=subject[:500],  # keep original subject for audit
        )
        
        try:
            session.add(ca)
            session.commit()
            summary["ingested"] += 1
            summary["by_type"][action_type] = summary["by_type"].get(action_type, 0) + 1
            if verbose:
                logger.info(f"Ingested {action_type} for {symbol} on {action_date}")
        except IntegrityError:
            session.rollback()
            summary["skipped_duplicate"] += 1
            logger.debug(f"Already ingested: {action_type} for {symbol} on {action_date}")
    
    # --- Final status ---
    if summary["error"] is None:
        summary["status"] = "success"
    
    logger.info(
        f"Corporate actions ingest: fetched={summary['fetched']}, "
        f"ingested={summary['ingested']}, "
        f"skipped_no_stock={summary['skipped_no_stock']}, "
        f"skipped_routine={summary['skipped_routine']}, "
        f"skipped_duplicate={summary['skipped_duplicate']}, "
        f"by_type={summary['by_type']}"
    )
    
    _write_ingestion_run(session, "corporate_actions", started_at, summary)
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
    logging.basicConfig(level=logging.INFO)
    summary = ingest_corporate_actions()
    print("\n=== Ingestion Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")