"""
DB resilience for serverless Postgres (Neon free tier).

The problem this solves: Neon scales to zero and closes idle connections. The
original pipeline held ONE session open for the entire ~40-minute run; during a
long news-ingestion step Neon hung up, and every subsequent query in that
session failed with 'server closed the connection' followed by a cascade of
'Can't reconnect until invalid transaction is rolled back'.

The fix (three parts):
  1. run_scorer_resilient(): gives each scorer its OWN fresh session, so one
     dropped connection can never poison another scorer. On a drop it rolls
     back, makes a new session, and retries the scorer once.
  2. write_scores_batched(): commits in chunks instead of one giant commit at
     the end, so a mid-write drop loses at most one chunk (which the retry
     redoes), not the whole vector.
  3. keepalive via pool_pre_ping (already set in get_engine) revalidates a
     connection before use, catching most stale-connection cases up front.

Drop-in: run_daily_v2 imports these instead of calling scorer methods directly.
Nothing in scorers/base.py needs to change.
"""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, PendingRollbackError, DBAPIError

from data.schema import VectorScore, get_session

logger = logging.getLogger("conflux.resilient")


def _fresh_session(max_tries: int = 4):
    """
    Return a session whose connection is proven ALIVE.

    This is the key fix: Neon drops the connection during the long ingestion
    phase, and the next DB op inherits a dead connection. score_universe()
    catches per-stock exceptions and logs them, so a drop on the FIRST stock
    never bubbles up to the retry wrapper — the whole vector then fails 1481
    times (exactly what happened to V2). By forcing a real SELECT 1 here and
    reconnecting until it succeeds, the scorer always STARTS on a live
    connection, so the swallowed-exception cascade can't happen.
    """
    for attempt in range(1, max_tries + 1):
        session = get_session()
        try:
            session.execute(text("SELECT 1"))
            return session
        except Exception as e:  # noqa: BLE001
            try:
                session.rollback()
                session.close()
            except Exception:
                pass
            wait = 3 * attempt
            logger.warning(f"pre-warm ping failed (attempt {attempt}/{max_tries}); "
                           f"reconnecting in {wait}s")
            time.sleep(wait)
    # last resort: hand back a session anyway; the caller's retry still guards it
    return get_session()

# psycopg raises these strings when Neon drops the connection
_DROP_MARKERS = (
    "server closed the connection",
    "consuming input failed",
    "connection already closed",
    "SSL connection has been closed",
    "terminating connection",
)


def _is_drop(exc: Exception) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, (OperationalError, PendingRollbackError, DBAPIError)) and \
        any(m.lower() in msg for m in _DROP_MARKERS) or isinstance(exc, PendingRollbackError)


def write_scores_batched(session, scorer, results: dict, asof, chunk: int = 100) -> int:
    """
    Upsert VectorScore rows in chunks, committing each chunk. Returns rows written.
    Mirrors the logic in VectorScorer.write_scores but commits incrementally so a
    dropped connection costs one chunk, not the whole vector.
    """
    items = list(results.items())
    written = 0
    for i in range(0, len(items), chunk):
        batch = items[i:i + chunk]
        for stock_id, res in batch:
            existing = (session.query(VectorScore)
                        .filter_by(stock_id=stock_id, vector_id=scorer.vector_id, date=asof)
                        .first())
            payload = dict(score=res.score, confidence=res.confidence,
                           rationale=res.rationale,
                           components_json=json.dumps(res.components, default=str))
            if existing:
                for k, v in payload.items():
                    setattr(existing, k, v)
            else:
                session.add(VectorScore(stock_id=stock_id, vector_id=scorer.vector_id,
                                        date=asof, **payload))
        session.commit()
        written += len(batch)
    return written


def run_scorer_resilient(scorer_cls, stocks, asof, max_retries: int = 2):
    """
    Run one scorer with its own fresh session. On a Neon connection drop, roll
    back, rebuild the session, and retry the whole scorer (scoring is pure /
    idempotent, so re-running is safe). Returns (n_written, n_universe).

    Isolation is the key property: a drop inside V6 cannot corrupt V1's run,
    because V1 gets a brand-new session when its turn comes.
    """
    attempt = 0
    while True:
        attempt += 1
        session = _fresh_session()          # proven-alive connection before we start
        scorer = scorer_cls(session=session)
        try:
            results = scorer.score_universe(stocks, asof)
            # If the scorer wrote nothing but the universe is non-trivial, a
            # swallowed mid-run drop is the likely cause — force a retry on a
            # fresh connection rather than accepting a false 0.
            if not results and len(stocks) > 50 and attempt <= max_retries:
                # verify the connection is still alive; if not, retry
                try:
                    session.execute(text("SELECT 1"))
                except Exception:
                    logger.warning(f"V{getattr(scorer,'vector_id','?')}: 0 results and "
                                   f"dead connection — retrying on fresh session")
                    session.close()
                    time.sleep(3 * attempt)
                    continue
            n = write_scores_batched(session, scorer, results, asof)
            logger.info(f"V{scorer.vector_id}: scored {n}/{len(stocks)} stocks")
            return n, len(stocks)
        except Exception as e:  # noqa: BLE001
            try:
                session.rollback()
            except Exception:
                pass
            if _is_drop(e) and attempt <= max_retries:
                wait = 3 * attempt
                logger.warning(f"V{getattr(scorer,'vector_id','?')}: connection dropped "
                               f"(attempt {attempt}/{max_retries}); reconnecting in {wait}s")
                time.sleep(wait)
                continue
            logger.exception(f"V{getattr(scorer,'vector_id','?')}: giving up after {attempt} attempts: {e}")
            return 0, len(stocks)
        finally:
            try:
                session.close()
            except Exception:
                pass


def run_confluence_resilient(compute_confluence, asof, max_retries: int = 2):
    """Same reconnect-and-retry wrapper for the confluence pass."""
    attempt = 0
    while True:
        attempt += 1
        session = _fresh_session()
        try:
            n = compute_confluence(asof, session=session)
            return n
        except Exception as e:  # noqa: BLE001
            try:
                session.rollback()
            except Exception:
                pass
            if _is_drop(e) and attempt <= max_retries:
                wait = 3 * attempt
                logger.warning(f"confluence: connection dropped "
                               f"(attempt {attempt}/{max_retries}); reconnecting in {wait}s")
                time.sleep(wait)
                continue
            logger.exception(f"confluence: giving up after {attempt} attempts: {e}")
            return 0
        finally:
            try:
                session.close()
            except Exception:
                pass
