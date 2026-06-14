"""
NSE session utility for fetching unofficial JSON endpoints.

NSE blocks naive requests with 403. Working approach:
1. Create a requests.Session with browser-like headers
2. Prime the session by GETting the homepage (yields cookies)
3. Use the same session for API calls (cookies authenticate)
4. Re-prime on cookie expiry (~10 min idle)
5. Rate limit ourselves to 1 req per 2 seconds

This module is the only place that talks to NSE directly. Upstream code
(V12 ingester) treats this as a black box that either returns data or
raises NSESessionError.

References:
- Endpoint: https://www.nseindia.com/api/corporates-corporateActions?index=equities
- License: NSE's public website data; for personal/research use.
  No bulk redistribution.
"""

import logging
import time
from typing import Optional

# Using curl_cffi instead of requests to bypass TLS fingerprinting.
# NSE (and many modern sites behind Cloudflare-like protection) check the
# TLS Client Hello signature. curl_cffi impersonates Chrome's fingerprint
# at the libcurl level — the request looks identical to a real browser
# on the wire. API is requests-compatible.
from curl_cffi import requests

logger = logging.getLogger(__name__)


# --- Configuration ---------------------------------------------------------

NSE_HOMEPAGE = "https://www.nseindia.com/"
NSE_CORP_ACTIONS_URL = (
    "https://www.nseindia.com/api/corporates-corporateActions?index=equities"
)

# Realistic browser headers. Without these, NSE returns 403.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}

MIN_REQUEST_INTERVAL_SEC = 2.0
MAX_RETRIES = 3
INITIAL_BACKOFF_SEC = 3.0


# --- Custom exception ------------------------------------------------------

class NSESessionError(Exception):
    """Raised when NSE data cannot be fetched after retries."""
    pass


# --- Session manager -------------------------------------------------------

class NSESession:
    """
    Manages a requests.Session against nseindia.com with cookie priming
    and rate limiting.
    
    Usage:
        with NSESession() as nse:
            actions = nse.fetch_corporate_actions()
    
    The session is primed lazily on first API call. Cookies expire after
    ~10 minutes of inactivity, so we re-prime if the last successful call
    was more than 8 minutes ago (safety margin).
    """
    
    COOKIE_TTL_SEC = 8 * 60  # re-prime after 8 min idle
    
    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._last_request_time: float = 0.0
        self._last_prime_time: float = 0.0
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
    
    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None
    
    def _rate_limit(self):
        """Sleep if needed to honor MIN_REQUEST_INTERVAL_SEC."""
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_SEC:
            sleep_for = MIN_REQUEST_INTERVAL_SEC - elapsed
            logger.debug(f"Rate limit: sleeping {sleep_for:.2f}s")
            time.sleep(sleep_for)
    
    def _ensure_primed(self):
        """
        Make sure session has fresh cookies. Creates session if none exists,
        re-primes if cookies are stale.
        """
        now = time.time()
        needs_prime = (
            self._session is None
            or (now - self._last_prime_time) > self.COOKIE_TTL_SEC
        )
        
        if not needs_prime:
            return
        
        if self._session is None:
            # impersonate="chrome120" makes curl_cffi mimic Chrome 120's
            # TLS fingerprint AND its header order/ja3. The header dict
            # below adds NSE-specific Referer on top of the impersonation.
            self._session = requests.Session(impersonate="chrome120")
            self._session.headers.update(BROWSER_HEADERS)
        
        self._rate_limit()
        logger.info("Priming NSE session (fetching homepage for cookies)")
        
        try:
            resp = self._session.get(NSE_HOMEPAGE, timeout=10)
            self._last_request_time = time.time()
        except requests.exceptions.RequestException as e:
            raise NSESessionError(
                f"Failed to reach NSE homepage during priming: {e}"
            ) from e
        
        if resp.status_code != 200:
            raise NSESessionError(
                f"NSE homepage returned {resp.status_code} during priming"
            )
        
        if not self._session.cookies:
            raise NSESessionError(
                "NSE homepage returned 200 but set no cookies; "
                "anti-bot rules may have changed."
            )
        
        self._last_prime_time = time.time()
        logger.info(f"NSE session primed with {len(self._session.cookies)} cookies")
    
    def fetch_json(self, url: str) -> dict:
        """
        Fetch a JSON URL with retries and cookie management.
        
        Raises NSESessionError on persistent failure.
        """
        backoff = INITIAL_BACKOFF_SEC
        last_error: Optional[Exception] = None
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._ensure_primed()
                self._rate_limit()
                
                logger.debug(f"NSE fetch attempt {attempt}: {url}")
                resp = self._session.get(url, timeout=15)
                self._last_request_time = time.time()
                
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as e:
                        # Server returned 200 but body isn't JSON
                        # (possible if NSE briefly serves an HTML error page)
                        last_error = NSESessionError(
                            f"NSE returned 200 but non-JSON body "
                            f"(first 200 chars): {resp.text[:200]}"
                        )
                        logger.warning(str(last_error))
                
                elif resp.status_code in (401, 403):
                    # Likely cookie expired or rotated. Re-prime and retry.
                    logger.warning(
                        f"NSE returned {resp.status_code}; "
                        f"re-priming and retrying (attempt {attempt}/{MAX_RETRIES})"
                    )
                    self._last_prime_time = 0  # force re-prime next call
                    last_error = NSESessionError(
                        f"NSE returned {resp.status_code} on attempt {attempt}"
                    )
                
                elif resp.status_code == 429:
                    # Rate limited. Back off harder.
                    logger.warning(
                        f"NSE returned 429 (rate limited); "
                        f"backing off {backoff * 2:.0f}s"
                    )
                    time.sleep(backoff * 2)
                    last_error = NSESessionError("NSE rate limited (429)")
                
                else:
                    last_error = NSESessionError(
                        f"NSE returned unexpected status {resp.status_code}"
                    )
                    logger.warning(str(last_error))
                
            except requests.RequestException as e:
                last_error = NSESessionError(f"Network error: {e}")
                logger.warning(f"Network error on attempt {attempt}: {e}")
            
            if attempt < MAX_RETRIES:
                logger.info(f"Waiting {backoff:.1f}s before retry")
                time.sleep(backoff)
                backoff *= 2
        
        # All retries exhausted
        raise NSESessionError(
            f"NSE fetch failed after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )
    
    def fetch_corporate_actions(self,
                                 from_date: Optional[str] = None,
                                 to_date: Optional[str] = None) -> list[dict]:
        """
        Fetch corporate actions from NSE for all equities.
        
        Args:
            from_date: 'DD-MM-YYYY' or None. If None, NSE returns default
                       window (~next 7 days only). Pass explicit dates for
                       historical windows.
            to_date:   'DD-MM-YYYY' or None.
        
        Returns: list of dicts in NSE's native shape. See module docstring
        for observed key names.
        
        Raises NSESessionError on persistent failure.
        """
        url = NSE_CORP_ACTIONS_URL
        params = []
        if from_date:
            params.append(f"from_date={from_date}")
        if to_date:
            params.append(f"to_date={to_date}")
        if params:
            url = f"{url}&{'&'.join(params)}"
        
        logger.info(f"Fetching NSE corporate actions (from={from_date}, to={to_date})")
        data = self.fetch_json(url)
        
        if isinstance(data, list):
            actions = data
        elif isinstance(data, dict):
            actions = data.get("data", data.get("rows", []))
        else:
            raise NSESessionError(
                f"Unexpected response type: {type(data).__name__}"
            )
        
        logger.info(f"NSE corporate actions: fetched {len(actions)} events")
        
        if len(actions) == 0:
            logger.warning(
                "NSE returned 0 corporate actions — possible silent failure "
                "(anti-bot rotation) or genuinely quiet window"
            )
        
        return actions


# --- Module-level convenience function ------------------------------------

def fetch_corporate_actions(from_date: Optional[str] = None,
                             to_date: Optional[str] = None) -> list[dict]:
    """
    Module-level convenience: fetch corporate actions in one call,
    auto-managing the session lifecycle.
    """
    with NSESession() as nse:
        return nse.fetch_corporate_actions(from_date=from_date, to_date=to_date)


if __name__ == "__main__":
    # Manual test entry point
    logging.basicConfig(level=logging.INFO)
    try:
        actions = fetch_corporate_actions()
        print(f"\nFetched {len(actions)} actions")
        if actions:
            print(f"First action keys: {list(actions[0].keys())}")
            print(f"First action: {actions[0]}")
    except NSESessionError as e:
        print(f"FAILED: {e}")