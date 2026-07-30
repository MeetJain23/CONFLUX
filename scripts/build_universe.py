"""
Universe builder — expands CONFLUX from the curated ~89 to (nearly) all of NSE.

    python -m scripts.build_universe                 # dry run: shows counts, writes nothing
    python -m scripts.build_universe --apply         # writes/updates stocks table
    python -m scripts.build_universe --apply --price-floor 30 --turnover-floor-cr 0.5

What it does:
1. Downloads the official NSE equity master (EQUITY_L.csv — every listed company).
2. Downloads the latest bhavcopy for close price + turnover.
3. Applies the penny/liquidity filter:
     - SERIES must be EQ (excludes BE/BZ trade-to-trade, SME, rights, warrants)
     - close >= --price-floor            (default ₹30)
     - daily turnover >= --turnover-floor-cr crores (default ₹0.50 cr)
4. Tier assignment:
     - Stocks already in your DB (the hand-curated moat) -> tier 1, untouched.
       Their sector/sub_sector/commodity mappings are NEVER overwritten.
     - New stocks -> tier 2 (automated vectors only: V12, V13-by-sector when
       sector is later mapped, price-derived). Honest lower coverage by design.
5. Adds the international watchlist (V11 parents + notable globals) as tier 2
   with yfinance symbols — these are priced by ingestion/prices.py, not bhavcopy.
6. Deactivates (active=False, never deletes) DB stocks that fail the filter
   or vanished from NSE — history is preserved, they just stop scoring.

Confidence-weighting already handles partial coverage: tier-2 stocks simply
have fewer active vectors and honest confidence. Glass-box applies to the
universe itself.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys

import pandas as pd
from curl_cffi import requests as curl_requests

from data.schema import Stock, get_session, init_db
from ingestion.bhavcopy import latest_trading_bhavcopy, HEADERS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# International tier-2 watchlist: V11 global parents first (they feed the
# parent-tracker), then benchmark globals. Extend freely — one line per stock.
INTERNATIONAL = [
    # (symbol_key, symbol_yf, name, sector)
    ("HITACHI_JP",  "6501.T",   "Hitachi Ltd",                 "Industrials"),
    ("SIEMENS_DE",  "SIE.DE",   "Siemens AG",                  "Industrials"),
    ("ABB_CH",      "ABBN.SW",  "ABB Ltd",                     "Industrials"),
    ("BOSCH_DE",    "BOSCHLTD.NS", None, None),  # placeholder guard, skipped
    ("HONEYWELL_US","HON",      "Honeywell International",     "Industrials"),
    ("APPLE_US",    "AAPL",     "Apple Inc",                   "Technology"),
    ("MICROSOFT_US","MSFT",     "Microsoft Corp",              "Technology"),
    ("NVIDIA_US",   "NVDA",     "NVIDIA Corp",                 "Technology"),
    ("TSMC_TW",     "TSM",      "Taiwan Semiconductor (ADR)",  "Technology"),
    ("TESLA_US",    "TSLA",     "Tesla Inc",                   "Auto"),
    ("TOYOTA_JP",   "7203.T",   "Toyota Motor Corp",           "Auto"),
    ("EXXON_US",    "XOM",      "Exxon Mobil",                 "Oil & Gas"),
    ("SHELL_UK",    "SHEL.L",   "Shell plc",                   "Oil & Gas"),
    ("RIO_UK",      "RIO.L",    "Rio Tinto",                   "Metals"),
    ("JPM_US",      "JPM",      "JPMorgan Chase",              "Banks"),
    ("UNILEVER_UK", "ULVR.L",   "Unilever plc",                "FMCG"),
    ("LVMH_FR",     "MC.PA",    "LVMH",                        "Consumer"),
    ("SAUDIARAMCO", "2222.SR",  "Saudi Aramco",                "Oil & Gas"),
]


def fetch_equity_master() -> pd.DataFrame:
    r = curl_requests.get(EQUITY_LIST_URL, headers=HEADERS, impersonate="chrome", timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"SYMBOL": "symbol", "NAME OF COMPANY": "name", "SERIES": "series"})
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["series"] = df["series"].astype(str).str.strip()
    logger.info(f"NSE equity master: {len(df)} listed instruments")
    return df[["symbol", "name", "series"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is dry run)")
    ap.add_argument("--price-floor", type=float, default=30.0, help="min close price ₹")
    ap.add_argument("--turnover-floor-cr", type=float, default=0.5, help="min daily turnover ₹cr")
    ap.add_argument("--skip-international", action="store_true")
    args = ap.parse_args()

    init_db()
    session = get_session()

    master = fetch_equity_master()
    got = latest_trading_bhavcopy()
    if not got:
        logger.error("Could not fetch a bhavcopy — run again later or from a non-blocked network.")
        sys.exit(1)
    asof, bhav = got
    logger.info(f"Filtering against bhavcopy of {asof}")

    eq = bhav[bhav["series"] == "EQ"][["symbol", "close", "turnover_cr"]]
    df = master[master["series"] == "EQ"].merge(eq, on="symbol", how="inner")

    before = len(df)
    df = df[(df["close"] >= args.price_floor) &
            (df["turnover_cr"].fillna(0) >= args.turnover_floor_cr)]
    logger.info(f"Penny/liquidity filter: {before} EQ stocks -> {len(df)} "
                f"(close>=₹{args.price_floor}, turnover>=₹{args.turnover_floor_cr}cr)")

    existing = {s.symbol_nse: s for s in session.query(Stock).all()}
    passing = set(df["symbol"])

    n_new = n_kept = n_deactivated = n_reactivated = 0
    for r in df.itertuples(index=False):
        s = existing.get(r.symbol)
        if s:
            # Curated moat row: touch nothing except reactivation. Tier stays as-is
            # (your original 89 stay tier 1 after the migration script tags them).
            if not s.active:
                n_reactivated += 1
                if args.apply:
                    s.active = True
            n_kept += 1
        else:
            n_new += 1
            if args.apply:
                session.add(Stock(symbol_nse=r.symbol, symbol_yf=f"{r.symbol}.NS",
                                  name=str(r.name)[:200], tier=2, active=True))

    for sym, s in existing.items():
        if s.symbol_nse.endswith("_INTL"):
            continue
        if sym not in passing and s.active:
            n_deactivated += 1
            if args.apply:
                s.active = False
                s.notes = ((s.notes or "") + f" | auto-deactivated {asof}: failed universe filter")[:2000]

    n_intl = 0
    if not args.skip_international:
        for key, yf_sym, name, sector in INTERNATIONAL:
            if name is None:
                continue
            sym = f"{key}_INTL"
            if sym not in existing:
                n_intl += 1
                if args.apply:
                    session.add(Stock(symbol_nse=sym, symbol_yf=yf_sym, name=name,
                                      sector=sector, tier=2, active=True,
                                      notes="international watchlist"))

    if args.apply:
        session.commit()

    mode = "APPLIED" if args.apply else "DRY RUN — re-run with --apply to write"
    total = session.query(Stock).filter(Stock.active == True).count() if args.apply \
        else n_kept + n_new + n_intl  # noqa: E712
    print(f"\n[{mode}]")
    print(f"  kept (curated + prior): {n_kept}   reactivated: {n_reactivated}")
    print(f"  new NSE tier-2 stocks:  {n_new}")
    print(f"  international tier-2:   {n_intl}")
    print(f"  deactivated:            {n_deactivated}")
    print(f"  active universe now:    {total}")
    print("\nTier-1 vectors (all 7) keep running on your curated stocks;")
    print("tier-2 stocks score on automatable vectors only, at honest confidence.")


if __name__ == "__main__":
    main()
