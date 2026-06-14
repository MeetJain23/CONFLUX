\## Pending metadata additions



\- TMPV (Tata Motors Passenger Vehicles): PV + JLR. Auto/Passenger sector. Steel + aluminium + rubber, with elevated aluminium for JLR.

\- TMCV (Tata Motors Commercial Vehicles): trucks + Iveco. Commercial vehicle sector. Steel-dominant, less aluminium.

\- Verify actual yfinance tickers before adding (likely TATAMOTORS.NS now refers to TMCV per the renaming).

\- Jindal Steel typo was fixed (JINDALSTL → JINDALSTEL) on \[date].

## Loader hardening (Phase 2)

- load_metadata.py is upsert-only — does not delete DB rows that no longer
  appear in CSVs. Caused TATAMOTORS ghost on 2026-06-13.
- Add sync mode: detect CSV-vs-DB drift, log clearly, gate deletions behind
  --sync flag to avoid accidental data loss on CSV typos.
- Until then: any time a stock or input link is removed from CSV, also run
  manual cleanup like the TATAMOTORS deletion script.

  ## V4 calibration observations (2026-06-13)

- Auto OEMs (M&M, BAJAJ-AUTO, HEROMOTOCO, EICHERMOT, TVSMOTOR) lack rubber
  rows — MARUTI has them. Either add rubber to all 6 for consistency, or
  remove from MARUTI. Decide during V12 work.

- Reliance V4 swung from -0.099 (Tue) to +0.159 (Fri) — 0.25 magnitude in
  4 days. Brent weight at 55% may be too aggressive. Calibration item for
  Phase 2 when more vectors are live to compare against.

- 6 auto OEMs score within 0.010 of each other on V4 alone. V4 cannot
  differentiate them — differentiation must come from other vectors
  (promoter quality, demand side, sub-sector). Honest gap.

## Loader hardening (Phase 2)

- load_metadata.py is upsert-only — does not delete DB rows that no longer
  appear in CSVs. Caused TATAMOTORS ghost on 2026-06-13.
- Add sync mode: detect CSV-vs-DB drift, log clearly, gate deletions behind
  --sync flag to avoid accidental data loss on CSV typos.

## SQLAlchemy 2.0 migration

- Query.get() deprecated. Update scripts/test_v4.py, scripts/test_v13.py,
  scripts/test_confluence.py: session.query(X).get(id) → session.get(X, id).
- datetime.utcnow() deprecated in schema.py. Use datetime.now(datetime.UTC).

## V13 sector-uniformity limitation (2026-06-13)

- V13 produces identical scores for all stocks within a sector. Mathematically
  inevitable given current design (sector × macros → one score).
- Example: all 9 metals at -0.281, all 6 autos at +0.319, all 4 oil & gas
  at -0.476.
- Implication: V13 contributes sector-rotation signal only. Stock-level
  differentiation must come from V4 + future per-stock vectors (V1, V11, V12).
- Phase 3 option: per-stock macro sensitivity coefficients (e.g., Hindalco
  more dollar-aluminium than Vedanta; JLR in TMPV adds GBP/EUR exposure).
  39 stocks × 5 macros = 195 hand-curated numbers. Real moat work but
  defer until other vectors are live.

  ## Session retrospective (2026-06-13)

- Path 4 (FRED INDIA10Y restore) — landed clean in ~90 min. FRED series
  code was wrong on first try (IRLTLT01INM156N → INDIRLTLT01STM). Web
  search caught it. Lesson: don't trust memorized series IDs for niche data.

- Universe expansion 15 → 39 — the work compounded. Same code, 2.5x more
  signal. Worth doing.

- TATAMOTORS demerger / ghost data — caught only because Claude diagnosed
  the V4 output, not because I'd have noticed at 2 AM. Two takeaways:
  (a) loader hardening is real Phase 2 work, (b) corporate actions
  feed will matter when V12 ships — IPOs and demergers are exactly what
  V12 should detect.

- V13 sector uniformity — known limitation, documented, will need
  per-stock macro sensitivities in Phase 3 to fix properly.

- Worked too late. Started Wednesday afternoon, didn't sleep before
  "Thursday morning" V4 run. Next time: actual sleep break between
  major sessions.

  ## Metadata folder reorganization (Phase 2 hygiene)

- Current state: metadata/ contains both curated user data (stocks.csv,
  stock_input_commodities.csv) and configuration (v12_event_magnitudes.csv).
  Gitignore explicitly lists curated files by name.
- Better structure: metadata/curated/ (gitignored) and metadata/config/
  (tracked). Single gitignore rule, semantic separation.
- Cost: ~45 min refactor — move 3 CSVs, update load_metadata.py and
  templates.py paths, update README and ADRs.
- Defer until: V12 + V2 are shipped, or before adding another config-type CSV.

## V12 ingester open questions (for Commit 4)

- NSE corporate actions API returned only 20 events with default params.
  Probably needs explicit from_date/to_date query params to get 60-day
  historical window for decay scoring.
- subject field is free-text, not controlled vocabulary. Need parser to map:
  "Buy Back of Shares" → BUYBACK
  "Bonus N:M" → BONUS
  "Stock Split..." → SPLIT
  "Demerger" → DEMERGER
  Routine "Dividend - Rs X Per Share" → IGNORE (most are not re-rating events)
  Large dividends (yield > 2%) → SPECIAL_DIVIDEND
- API may only return upcoming events. Need to verify if historical
  events are accessible or if we need an archive endpoint.
- Endpoint coverage gap: board changes and promoter pledging are likely
  NOT in this feed. Probably need separate endpoints or alternative
  sources. Phase 2 problem.

  added corporate actions