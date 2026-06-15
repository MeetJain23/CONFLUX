# CONFLUX TODO

Tracking known issues, Phase 2 work, and deferred decisions. Sectioned by concern.

## Pending metadata additions

- TMPV (Tata Motors Passenger Vehicles): PV + JLR. Auto/Passenger sector.
  Steel + aluminium + rubber, with elevated aluminium for JLR.
- TMCV (Tata Motors Commercial Vehicles): trucks + Iveco. Commercial vehicle
  sector. Steel-dominant, less aluminium.
- Verify actual yfinance tickers before adding (likely TATAMOTORS.NS now
  refers to TMCV per the renaming after the Oct 2025 demerger).

## Loader hardening (Phase 2)

- `scripts/load_metadata.py` is upsert-only — does not delete DB rows that
  no longer appear in CSVs. Caused TATAMOTORS ghost on 2026-06-13.
- Add sync mode: detect CSV-vs-DB drift, log clearly, gate deletions
  behind `--sync` flag to avoid accidental data loss on CSV typos.
- Until then: any time a stock or input link is removed from CSV, also
  run manual cleanup (see TATAMOTORS deletion pattern in session notes).

## V4 calibration observations (2026-06-13)

- Auto OEMs (M&M, BAJAJ-AUTO, HEROMOTOCO, EICHERMOT, TVSMOTOR) lack rubber
  rows — MARUTI has them. Either add rubber to all 6 for consistency, or
  remove from MARUTI.
- Reliance V4 swung from -0.099 to +0.159 in 4 days. Brent weight at 55%
  may be too aggressive. Calibration item for Phase 2.
- 6 auto OEMs score within 0.010 of each other on V4 alone. V4 cannot
  differentiate them — differentiation must come from other vectors.
  Honest gap, not a bug.

## V13 sector-uniformity limitation (2026-06-13)

- V13 produces identical scores for all stocks within a sector.
  Mathematically inevitable given current design (sector × macros = one score).
- Example: all 9 metals at -0.281, all 6 autos at +0.319, all 4 oil & gas
  at -0.476.
- Implication: V13 contributes sector-rotation signal only. Stock-level
  differentiation must come from V4 + future per-stock vectors (V1, V11, V12).
- Phase 3 option: per-stock macro sensitivity coefficients (e.g., Hindalco
  more dollar-aluminium than Vedanta; JLR in TMPV adds GBP/EUR exposure).
  39 stocks × 5 macros = 195 hand-curated numbers. Real moat work, defer
  until other vectors are live.

## V12 calibration (Phase 2)

- Magnitudes and decay windows in `v12_event_magnitudes.csv` are initial
  guesses, not calibrated against real market behavior.
- Phase 1 chose 60-day decay across most event types for simplicity.
- Phase 2 work: track V12 signal vs forward stock returns over 3-6 months.
  Tune per-event-type magnitudes based on which events actually predict
  re-rating moves.
- Specific questions to answer with real data:
  - Do buybacks actually drive 0.40 issuer magnitude worth of move, or less?
  - How long does demerger announcement signal persist? (60 days is a guess)
  - Are peer signals real or noise? (current 0.05-0.10 peer magnitudes are
    intuition, not evidence)
- Open question: should magnitudes scale with event size (buyback % of mcap,
  dividend yield, etc.) rather than flat per type?

## V12 ingester gaps

- Yield-based dividend tiering deferred. Currently all special/interim
  dividends get full magnitude regardless of yield. Phase 2: large
  dividends (yield > 2% of market cap) get full magnitude, smaller ones
  get half magnitude.
- Board changes and promoter pledging are NOT in NSE corporate-actions
  feed. Need separate endpoints (NSDL for pledging) or alternative sources.
- BSE corporate actions ingester deferred until universe expands past
  Nifty 100 (current 39-stock universe is fully NSE-covered).

## Metadata folder reorganization (Phase 2 hygiene)

- Current state: `metadata/` contains both curated user data (stocks.csv,
  stock_input_commodities.csv) and configuration (v12_event_magnitudes.csv).
  Gitignore explicitly lists curated files by name.
- Better structure: `metadata/curated/` (gitignored) and `metadata/config/`
  (tracked). Single gitignore rule, semantic separation.
- Cost: ~45 min refactor — move 3 CSVs, update load_metadata.py and
  templates.py paths, update README and ADRs.
- Defer until: V2 is shipped, or before adding another config-type CSV.

## SQLAlchemy 2.0 migration

- `Query.get()` deprecated. Update test scripts that still use it:
  `session.query(X).get(id)` → `session.get(X, id)`.
- `datetime.utcnow()` deprecated in `schema.py`. Use
  `datetime.now(timezone.utc).replace(tzinfo=None)`.

## Process notes (lessons from build sessions)

- Don't trust memorized identifiers for niche data sources. FRED series ID
  was wrong on first try (IRLTLT01INM156N vs INDIRLTLT01STM); web search
  caught it.
- Late-night work produces rough commits. Pattern observed across the
  project. Forward-looking discipline: write commit message before
  `git commit`. If you can't articulate the change cleanly, the commit
  is bundling too much or you're too tired.
- Verification gates matter. TATAMOTORS ghost, IngestionRun field bug,
  dashboard date default — all caught at "before commit" verification
  steps, not during code review. Re-run, query DB, eyeball output.