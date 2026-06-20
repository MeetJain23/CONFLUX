"""
V2 (Government Policy) — news ingester.

Pulls news items from Google News RSS using a curated query list, classifies
each into our controlled `subtype` vocabulary via regex, and upserts into
the policy_events table.

Design notes (per ADR-003):
- Single source for Phase 1: Google News RSS. PIB deferred to Phase 2
  (Google News covers PIB content via downstream news outlet reporting).
- Lookback default 14 days. Items older are skipped. Idempotency via
  unique constraint on (subtype, event_date, source_url).
- Classification is priority-ordered regex (first match wins). Same
  pattern as V12 corporate_actions classifier.
- Unmapped items (no subtype regex matched) are NOT stored. We only
  persist events we have a subtype for. Reduces noise in DB.

Phase 2 TODOs:
- PIB RSS as secondary source with deduplication
- Per-query priority in `v2_policy_queries.csv` already structured; use
  it to enable "quick" vs "full" ingestion modes
- Yield-aware classification (small policy item vs major scheme)
"""

import csv
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import feedparser

from sqlalchemy.exc import IntegrityError

from data.schema import IngestionRun, PolicyEvent, get_session

logger = logging.getLogger(__name__)


# --- Config -------------------------------------------------------------

QUERIES_CSV_PATH = Path("metadata/v2_policy_queries.csv")
DEFAULT_LOOKBACK_DAYS = 14
SLEEP_BETWEEN_QUERIES_SEC = 1.5  # polite pacing for Google News RSS

GOOGLE_NEWS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
)


# --- Classification rules ------------------------------------------------
#
# Priority-ordered. First pattern that matches the lowercased text wins.
# Text = entry.title + " " + entry.summary
#
# When adding new patterns: place most specific FIRST. Generic patterns
# (like "duty") would eat specific ones (like "anti-dumping duty") if
# the generic came first.

CLASSIFICATION_PATTERNS = [
    # PLI schemes - most specific sector patterns first
    (re.compile(r"\bpli\b.*\bsemiconductor"), "PLI_SEMICONDUCTORS"),
    (re.compile(r"\bsemiconductor.*\b(pli|incentive)"), "PLI_SEMICONDUCTORS"),
    (re.compile(r"\bpli\b.*\bauto\s+components?\b"), "PLI_AUTO_COMPONENTS"),
    (re.compile(r"\bpli\b.*\bev\b|\bev\b.*\bpli\b"), "PLI_AUTO_COMPONENTS"),
    (re.compile(r"\bpli\b.*\b(pharma|api|bulk\s+drug)"), "PLI_PHARMA"),
    (re.compile(r"\bpli\b.*\btextile"), "PLI_TEXTILES"),
    (re.compile(r"\bpli\b.*\b(electronics|mobile)"), "PLI_ELECTRONICS"),
    (re.compile(r"\bpli\b.*\bsolar"), "PLI_SOLAR"),
    (re.compile(r"\bpli\b.*\btelecom"), "PLI_TELECOM"),
    (re.compile(r"\bpli\b.*\bfood\s+processing"), "PLI_FOOD_PROCESSING"),
    
    # Anti-dumping (commodity-specific first)
    (re.compile(r"\banti.?dumping.*\bsteel"), "ANTI_DUMPING_STEEL"),
    (re.compile(r"\banti.?dumping.*\b(chemical|chemicals)"), "ANTI_DUMPING_CHEMICALS"),
    (re.compile(r"\banti.?dumping.*\bsolar"), "ANTI_DUMPING_SOLAR"),
    (re.compile(r"\banti.?dumping.*\baluminium|\baluminum\b"), "ANTI_DUMPING_ALUMINIUM"),
    
    # Tariff and duty changes
    (re.compile(r"\b(tariff|duty)\s+(hike|increase|raised).*\bsteel"), "TARIFF_INCREASE_STEEL"),
    (re.compile(r"\b(tariff|duty)\s+(hike|increase|raised).*\bcrude"), "TARIFF_INCREASE_CRUDE"),
    (re.compile(r"\b(tariff|duty)\s+(cut|reduced|lowered).*\bgold"), "TARIFF_DECREASE_GOLD"),
    (re.compile(r"\b(tariff|duty)\s+(hike|increase|raised).*\bgold"), "TARIFF_INCREASE_GOLD"),
    (re.compile(r"\bcustoms?\s+duty\s+(hike|increase|raised).*\bsteel"), "DUTY_INCREASE_STEEL"),
    (re.compile(r"\bcustoms?\s+duty\s+(cut|reduced|lowered).*\bcrude"), "DUTY_DECREASE_CRUDE"),
    
    # GST changes
    (re.compile(r"\bgst.*\b(cut|reduced|lowered).*\b(hotel|hospitality)"), "GST_DECREASE_HOSPITALITY"),
    (re.compile(r"\bgst.*\b(hike|increase|raised).*\btobacco"), "GST_INCREASE_TOBACCO"),
    (re.compile(r"\bgst.*\b(rationalization|simplification)"), "GST_RATIONALIZATION"),
    
    # Budget allocations
    (re.compile(r"\b(budget|allocation).*\bdefence"), "BUDGET_DEFENCE"),
    (re.compile(r"\b(budget|allocation).*\binfrastructure"), "BUDGET_INFRASTRUCTURE"),
    (re.compile(r"\b(budget|allocation).*\brailway"), "BUDGET_RAILWAYS"),
    (re.compile(r"\b(budget|allocation).*\b(renewable|green\s+energy)"), "BUDGET_RENEWABLE_ENERGY"),
    (re.compile(r"\b(budget|allocation).*\bagriculture"), "BUDGET_AGRICULTURE"),
    
    # Privatization
    (re.compile(r"\bprivati[sz]ation.*\bbank"), "PRIVATIZATION_BANK"),
    (re.compile(r"\bprivati[sz]ation.*\boil"), "PRIVATIZATION_OIL"),
    
    # Subsidies (sector-specific)
    (re.compile(r"\bfertili[sz]er\s+subsidy"), "SUBSIDY_FERTILIZER"),
    (re.compile(r"\bsolar.*\bsubsidy"), "SUBSIDY_SOLAR"),
    
    # RBI rates
    (re.compile(r"\brbi.*\brepo\s+rate.*\b(cut|reduce|lower)"), "RBI_RATE_CUT"),
    (re.compile(r"\brbi.*\brepo\s+rate.*\b(hike|increase|raise)"), "RBI_RATE_HIKE"),
    
    # SEBI
    (re.compile(r"\bsebi.*\b(stricter|tightening|tighter)\s+disclosure"), "SEBI_DISCLOSURE_TIGHTENING"),
]


def classify_item(title: str, summary: str = "") -> Optional[str]:
    """
    Map a news item's title+summary to our controlled subtype vocabulary.
    Returns None if no pattern matches (item will not be stored).
    """
    text = (title + " " + summary).lower()
    for pattern, subtype in CLASSIFICATION_PATTERNS:
        if pattern.search(text):
            return subtype
    return None


# --- Date parsing -------------------------------------------------------

def _parse_entry_date(entry) -> Optional[date]:
    """Extract a date from a feedparser entry. Prefers published_parsed."""
    parsed = getattr(entry, "published_parsed", None)
    if parsed:
        try:
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
        except (ValueError, TypeError):
            pass
    return None


# --- Query list loader --------------------------------------------------

def _load_queries(csv_path: Path = QUERIES_CSV_PATH) -> list[str]:
    """Read query list from CSV. Returns ordered list of query strings."""
    if not csv_path.exists():
        raise FileNotFoundError(
            f"V2 queries CSV not found at {csv_path}. "
            "Did you commit metadata/v2_policy_queries.csv?"
        )
    with open(csv_path, encoding="utf-8") as f:
        return [row["query"] for row in csv.DictReader(f)]


# --- Main ingester ------------------------------------------------------

def ingest_policy_news(lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                       session=None,
                       verbose: bool = False) -> dict:
    """
    Fetch Google News RSS for each query in v2_policy_queries.csv, classify,
    upsert to policy_events table.
    
    Args:
        lookback_days: skip entries older than this. Default 14.
        session: optional SQLAlchemy session.
        verbose: log each ingested event individually.
    
    Returns summary dict matching V12 ingester's shape:
        {
            "status": "success" | "partial" | "failure",
            "fetched": <total entries across all queries>,
            "classified": <items matching a subtype>,
            "ingested": <new rows written>,
            "skipped_old": <items past lookback>,
            "skipped_unclassified": <no subtype matched>,
            "skipped_duplicate": <already in DB>,
            "by_subtype": {subtype: count},
            "error": <str | None>,
        }
    """
    session = session or get_session()
    queries = _load_queries()
    cutoff_date = date.today() - timedelta(days=lookback_days)
    
    summary = {
        "status": "failure",
        "fetched": 0,
        "classified": 0,
        "ingested": 0,
        "skipped_old": 0,
        "skipped_unclassified": 0,
        "skipped_duplicate": 0,
        "by_subtype": {},
        "error": None,
    }
    
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    try:
        for i, query in enumerate(queries):
            if i > 0:
                time.sleep(SLEEP_BETWEEN_QUERIES_SEC)
            
            url = GOOGLE_NEWS_URL_TEMPLATE.format(q=quote_plus(query))
            logger.debug(f"Fetching: {query}")
            
            feed = feedparser.parse(url)
            
            if not hasattr(feed, "entries") or not feed.entries:
                logger.warning(f"No entries for query: {query!r}")
                continue
            
            summary["fetched"] += len(feed.entries)
            
            for entry in feed.entries:
                event_date = _parse_entry_date(entry)
                if event_date is None or event_date < cutoff_date:
                    summary["skipped_old"] += 1
                    continue
                
                title = getattr(entry, "title", "")
                summary_text = getattr(entry, "summary", "")
                
                subtype = classify_item(title, summary_text)
                if subtype is None:
                    summary["skipped_unclassified"] += 1
                    continue
                
                summary["classified"] += 1
                
                link = getattr(entry, "link", None)
                
                pe = PolicyEvent(
                    subtype=subtype,
                    event_date=event_date,
                    headline_text=title[:1000],
                    source="GOOGLE_NEWS",
                    source_url=link[:512] if link else None,
                    raw_payload={
                        "title": title,
                        "link": link,
                        "published": getattr(entry, "published", None),
                        "summary": summary_text[:500] if summary_text else None,
                        "source_name": getattr(entry, "source", {}).get("title") if hasattr(entry, "source") else None,
                    },
                    notes=f"Query: {query!r}"[:500],
                )
                
                try:
                    session.add(pe)
                    session.commit()
                    summary["ingested"] += 1
                    summary["by_subtype"][subtype] = summary["by_subtype"].get(subtype, 0) + 1
                    if verbose:
                        logger.info(f"Ingested {subtype} ({event_date}): {title[:80]}")
                except IntegrityError:
                    session.rollback()
                    summary["skipped_duplicate"] += 1
                    logger.debug(f"Duplicate: {subtype} on {event_date} from {link}")
        
        summary["status"] = "success"
    
    except Exception as e:
        summary["error"] = str(e)
        logger.exception(f"Policy news ingestion failed: {e}")
    
    logger.info(
        f"Policy news ingest: fetched={summary['fetched']}, "
        f"classified={summary['classified']}, "
        f"ingested={summary['ingested']}, "
        f"skipped_old={summary['skipped_old']}, "
        f"skipped_unclassified={summary['skipped_unclassified']}, "
        f"skipped_duplicate={summary['skipped_duplicate']}, "
        f"by_subtype={summary['by_subtype']}"
    )
    
    _write_ingestion_run(session, "policy_news", started_at, summary)
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


# --- Module test entry point --------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    summary = ingest_policy_news(verbose=True)
    print("\n=== Policy News Ingestion Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")