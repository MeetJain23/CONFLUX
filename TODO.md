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