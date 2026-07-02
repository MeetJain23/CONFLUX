"""
V6 (Supply Disruption) — news ingester.

Pulls news items from Google News RSS using v6_supply_queries.csv,
classifies each into our SUPPLY_ subtype vocabulary via regex, extracts
source_country + geography_scope + event_severity heuristics, and upserts
into the supply_events table.

Design notes:
- V6 is isolated from V2 by design (see project ADR discussion). Same
  RSS mechanism, separate ingester, separate table, separate metadata.
  This protects V2's stable production behaviour while V6 calibrates.
- Lookback default 14 days matches V2. Idempotency via unique constraint
  on (subtype, event_date, source_url).
- Classification is priority-ordered regex, most-specific-first. Same
  pattern as V2's policy_news classifier.
- Unmapped items (no subtype regex matched) are NOT stored — reduces
  DB noise, matches V2's approach.
- source_country and geography_scope are lightweight extractors: pattern-
  based inference from the classified subtype and headline. Not exact
  NER; good enough for Phase 2 calibration hooks.

Phase 2 TODOs:
- IMD weather portal as authoritative source for MONSOON_* (Google News
  currently catches these via mainstream outlets; direct feed reduces lag)
- OPEC press release RSS for OPEC_CUT (leading vs lagging signal)
- MOSPI gas allocation notices for NATURAL_GAS_SHORTAGE
- Customs data pipeline for CHINA_API_DUMP verification
- event_severity extraction currently returns None; Phase 2 adds coarse
  0-1 scoring via headline keyword strength ("major", "brief", "full",
  "partial", etc.)
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

from data.schema import IngestionRun, SupplyEvent, get_session

logger = logging.getLogger(__name__)


# --- Config -------------------------------------------------------------

QUERIES_CSV_PATH = Path("metadata/v6_supply_queries.csv")
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
# Ordering principle: most specific FIRST. Source-country-qualified
# patterns (like "china steel cut") precede generic subtype patterns.

CLASSIFICATION_PATTERNS = [
   # Monsoon — DEFICIT first (bearish patterns). Match rainfall/rain/monsoon
    # variants. Handle hyphen and space in "below-normal" / "below normal".
    (re.compile(r"\bbelow.?normal\s+(rainfall|monsoon|rain)"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\b(deficient|weak|poor|drier)\s+(monsoon|rainfall|rain)"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\b(monsoon|rainfall)\s+deficit"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\bmonsoon.*(below.?normal|deficit|deficient|weak)"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\b(drier|hotter)\s+than\s+normal"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\bel\s?nino.*(monsoon|rainfall|rain|kharif)"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\b(monsoon|rainfall|kharif).*el\s?nino"), "SUPPLY_MONSOON_DEFICIT"),
    (re.compile(r"\bkharif\s+sowing.*(down|hit|weak)"), "SUPPLY_MONSOON_DEFICIT"),

    # Monsoon — NORMAL_ABOVE (bullish patterns). Require EXPLICIT positive
    # qualifier. Removed the loose `imd.*forecast.*normal` catch-all —
    # it was matching "IMD forecast below-normal" via greedy `.*`.
    (re.compile(r"\babove.?normal\s+(rainfall|monsoon|rain)"), "SUPPLY_MONSOON_NORMAL_ABOVE"),
    (re.compile(r"\b(surplus|excess|good|strong|robust)\s+(monsoon|rainfall|rain)"), "SUPPLY_MONSOON_NORMAL_ABOVE"),
    (re.compile(r"\bnormal\s+to\s+(excess|above)"), "SUPPLY_MONSOON_NORMAL_ABOVE"),
    (re.compile(r"\bla\s?nina.*(monsoon|rainfall|rain|kharif)"), "SUPPLY_MONSOON_NORMAL_ABOVE"),


    # Hormuz / Gulf shipping (regional; direction always negative for importers)
    (re.compile(r"\bstrait\s+of\s+hormuz|hormuz\s+(disruption|blockade|closure|attack)"), "SUPPLY_HORMUZ_GULF_DISRUPTION"),
    (re.compile(r"\btanker\s+attack.*(gulf|hormuz|persian)"), "SUPPLY_HORMUZ_GULF_DISRUPTION"),
    (re.compile(r"\bpersian\s+gulf\s+(disruption|closure)"), "SUPPLY_HORMUZ_GULF_DISRUPTION"),
    (re.compile(r"\bred\s+sea\s+(shipping|attack|disruption|houthi)"), "SUPPLY_HORMUZ_GULF_DISRUPTION"),
    (re.compile(r"\biran.*israel.*(oil|shipping|tanker|crude)"), "SUPPLY_HORMUZ_GULF_DISRUPTION"),

    # China steel (source-qualified; direction positive for Indian producers)
    (re.compile(r"\bchina.*steel.*(cut|production\s+cut|reform|reduce)"), "SUPPLY_CHINA_STEEL_CUT"),
    (re.compile(r"\bchina.*steel.*export.*(curb|restrict|ban)"), "SUPPLY_CHINA_STEEL_CUT"),
    (re.compile(r"\bchinese\s+steel\s+(production\s+cut|export\s+curb)"), "SUPPLY_CHINA_STEEL_CUT"),

    # China API — TWO opposite directions depending on qualifier
    # Dumping = China exporting cheap, hurts Indian producers
    # Disruption = China supply cut, helps Indian producers
    (re.compile(r"\bchina.*api.*(dump|dumping|undercut|cheap)"), "SUPPLY_CHINA_API_DUMP"),
    (re.compile(r"\bchinese\s+api\s+(dumping|undercutting)"), "SUPPLY_CHINA_API_DUMP"),
    (re.compile(r"\bchina.*api.*(export\s+ban|export\s+curb|shutdown|closure)"), "SUPPLY_PHARMA_API_DISRUPTION"),
    (re.compile(r"\bchina.*pharma.*plant.*(shutdown|closure|closed)"), "SUPPLY_PHARMA_API_DISRUPTION"),
    (re.compile(r"\bchinese\s+api.*(disruption|shortage)"), "SUPPLY_PHARMA_API_DISRUPTION"),

    # Critical minerals (China-sourced; direction negative for consumers)
    (re.compile(r"\bchina.*(rare\s+earth|gallium|germanium|graphite).*export"), "SUPPLY_CRITICAL_MINERALS_CURB"),
    (re.compile(r"\brare\s+earth.*export.*(curb|ban|restrict)"), "SUPPLY_CRITICAL_MINERALS_CURB"),
    (re.compile(r"\b(gallium|germanium|graphite).*export.*(curb|ban|control)"), "SUPPLY_CRITICAL_MINERALS_CURB"),

    # Semiconductor (global; direction negative for OEMs)
    (re.compile(r"\bsemiconductor\s+shortage"), "SUPPLY_SEMICONDUCTOR_SHORTAGE"),
    (re.compile(r"\bchip\s+shortage|chip\s+allocation\s+(cut|reduce)"), "SUPPLY_SEMICONDUCTOR_SHORTAGE"),
    (re.compile(r"\btsmc.*(capacity|allocation).*(cut|constraint)"), "SUPPLY_SEMICONDUCTOR_SHORTAGE"),
    (re.compile(r"\basml.*export.*(restrict|curb|ban)"), "SUPPLY_SEMICONDUCTOR_SHORTAGE"),

    # OPEC (global; direction negative for refiners, positive for upstream)
    (re.compile(r"\bopec\+?.*(production\s+cut|output\s+cut|cut\s+production)"), "SUPPLY_OPEC_CUT"),
    (re.compile(r"\bopec\+?\s+meeting.*(cut|reduce)"), "SUPPLY_OPEC_CUT"),
    (re.compile(r"\bsaudi.*(oil|crude).*production.*cut"), "SUPPLY_OPEC_CUT"),

    # Natural gas (India-specific allocation events)
    (re.compile(r"\bnatural\s+gas\s+(shortage|allocation\s+cut)"), "SUPPLY_NATURAL_GAS_SHORTAGE"),
    (re.compile(r"\bapm\s+gas\s+(allocation|cut|reduce)"), "SUPPLY_NATURAL_GAS_SHORTAGE"),
    (re.compile(r"\bgas\s+allocation.*(cgd|fertilizer|city\s+gas)"), "SUPPLY_NATURAL_GAS_SHORTAGE"),

    # Global tariff shocks (Section 232/301 style)
    (re.compile(r"\bsection\s+232.*tariff"), "SUPPLY_GLOBAL_TARIFF_SHOCK"),
    (re.compile(r"\bsection\s+301.*(tariff|china)"), "SUPPLY_GLOBAL_TARIFF_SHOCK"),
    (re.compile(r"\breciprocal\s+tariff"), "SUPPLY_GLOBAL_TARIFF_SHOCK"),
    (re.compile(r"\bus\s+tariff.*(india|steel|pharma)"), "SUPPLY_GLOBAL_TARIFF_SHOCK"),

    # Force majeure (catch-all; must be LAST since it's broad)
    (re.compile(r"\bforce\s+majeure"), "SUPPLY_FORCE_MAJEURE"),
    (re.compile(r"\bplant\s+(fire|shutdown|outage)"), "SUPPLY_FORCE_MAJEURE"),
    (re.compile(r"\bport\s+strike"), "SUPPLY_FORCE_MAJEURE"),
]


# --- Geography / source-country extraction ------------------------------
#
# Lightweight inference from subtype + text. Not exhaustive NER — good
# enough for Phase 2 calibration hooks.

SUBTYPE_TO_GEOGRAPHY = {
    "SUPPLY_MONSOON_DEFICIT": "india_specific",
    "SUPPLY_MONSOON_NORMAL_ABOVE": "india_specific",
    "SUPPLY_HORMUZ_GULF_DISRUPTION": "regional",
    "SUPPLY_CHINA_STEEL_CUT": "global",
    "SUPPLY_CHINA_API_DUMP": "global",
    "SUPPLY_PHARMA_API_DISRUPTION": "global",
    "SUPPLY_CRITICAL_MINERALS_CURB": "global",
    "SUPPLY_SEMICONDUCTOR_SHORTAGE": "global",
    "SUPPLY_OPEC_CUT": "global",
    "SUPPLY_NATURAL_GAS_SHORTAGE": "india_specific",
    "SUPPLY_GLOBAL_TARIFF_SHOCK": "global",
    "SUPPLY_FORCE_MAJEURE": None,  # varies per event
}

SUBTYPE_TO_DEFAULT_COUNTRY = {
    "SUPPLY_MONSOON_DEFICIT": "India",
    "SUPPLY_MONSOON_NORMAL_ABOVE": "India",
    "SUPPLY_HORMUZ_GULF_DISRUPTION": "Iran",  # most common origin
    "SUPPLY_CHINA_STEEL_CUT": "China",
    "SUPPLY_CHINA_API_DUMP": "China",
    "SUPPLY_PHARMA_API_DISRUPTION": "China",
    "SUPPLY_CRITICAL_MINERALS_CURB": "China",
    "SUPPLY_SEMICONDUCTOR_SHORTAGE": None,  # varies (Taiwan / US / global)
    "SUPPLY_OPEC_CUT": "Saudi Arabia",
    "SUPPLY_NATURAL_GAS_SHORTAGE": "India",
    "SUPPLY_GLOBAL_TARIFF_SHOCK": "USA",  # most common source of tariff shocks
    "SUPPLY_FORCE_MAJEURE": None,
}


def infer_source_country(subtype: str, text: str) -> Optional[str]:
    """
    Lightweight source-country inference. Text-based override for cases
    where the default is wrong (e.g., Houthi-driven Red Sea attributed to
    Yemen not Iran).
    """
    text_low = text.lower()

    # Text-level overrides (order matters; specific before general)
    if "houthi" in text_low or "yemen" in text_low:
        return "Yemen"
    if "russia" in text_low and subtype in ("SUPPLY_OPEC_CUT",):
        return "Russia"
    if "taiwan" in text_low and subtype == "SUPPLY_SEMICONDUCTOR_SHORTAGE":
        return "Taiwan"
    if "netherlands" in text_low and subtype == "SUPPLY_SEMICONDUCTOR_SHORTAGE":
        return "Netherlands"

    return SUBTYPE_TO_DEFAULT_COUNTRY.get(subtype)


def infer_geography_scope(subtype: str) -> Optional[str]:
    return SUBTYPE_TO_GEOGRAPHY.get(subtype)


def classify_item(title: str, summary: str = "") -> Optional[str]:
    """
    Map a news item's title+summary to our SUPPLY_ subtype vocabulary.
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
            f"V6 queries CSV not found at {csv_path}. "
            "Did you create metadata/v6_supply_queries.csv?"
        )
    with open(csv_path, encoding="utf-8") as f:
        return [row["query"] for row in csv.DictReader(f)]


# --- Main ingester ------------------------------------------------------

def ingest_supply_news(lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                       session=None,
                       verbose: bool = False) -> dict:
    """
    Fetch Google News RSS for each query in v6_supply_queries.csv, classify,
    extract source_country/geography_scope, upsert to supply_events table.

    Args:
        lookback_days: skip entries older than this. Default 14.
        session: optional SQLAlchemy session.
        verbose: log each ingested event individually.

    Returns summary dict matching V2 ingester's shape:
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

            try:
                feed = feedparser.parse(url)
            except Exception as fetch_err:
                logger.warning(f"Fetch failed for query {query!r}: {fetch_err}")
                summary["fetch_errors"] = summary.get("fetch_errors", 0) + 1
                continue

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
                combined_text = title + " " + summary_text

                source_country = infer_source_country(subtype, combined_text)
                geography_scope = infer_geography_scope(subtype)

                se = SupplyEvent(
                    subtype=subtype,
                    event_date=event_date,
                    headline_text=title[:1000],
                    source_country=source_country[:64] if source_country else None,
                    geography_scope=geography_scope[:32] if geography_scope else None,
                    event_severity=None,  # Phase 2 hook
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
                    session.add(se)
                    session.commit()
                    summary["ingested"] += 1
                    summary["by_subtype"][subtype] = summary["by_subtype"].get(subtype, 0) + 1
                    if verbose:
                        logger.info(f"Ingested {subtype} ({event_date}, {source_country}): {title[:80]}")
                except IntegrityError:
                    session.rollback()
                    summary["skipped_duplicate"] += 1
                    logger.debug(f"Duplicate: {subtype} on {event_date} from {link}")

        if summary.get("fetch_errors", 0) > 0:
            summary["status"] = "partial"
        else:
            summary["status"] = "success"

    except Exception as e:
        summary["error"] = str(e)
        summary["status"] = "failure" if summary["ingested"] == 0 else "partial"
        logger.exception(f"Supply news ingestion failed: {e}")

    logger.info(
        f"Supply news ingest: fetched={summary['fetched']}, "
        f"classified={summary['classified']}, "
        f"ingested={summary['ingested']}, "
        f"skipped_old={summary['skipped_old']}, "
        f"skipped_unclassified={summary['skipped_unclassified']}, "
        f"skipped_duplicate={summary['skipped_duplicate']}, "
        f"by_subtype={summary['by_subtype']}"
    )

    _write_ingestion_run(session, "supply_news", started_at, summary)
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
    summary = ingest_supply_news(verbose=True)
    print("\n=== Supply News Ingestion Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")