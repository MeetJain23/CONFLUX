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


  ## V2 classification tuning (Phase 2)

- 281 items in first V2 ingestion run were skipped as unclassified.
  Some are real signal we'd want to catch:
  - "RBI keeps repo rate unchanged" — no match (only "cut"/"hike" patterns)
  - "FDI relaxation in defence" — no FDI patterns yet
  - Semiconductor mission updates without word "PLI"
- Phase 2 work: log sample unclassified titles to file, manually review
  in batches, add regex patterns for genuine missed signal.
- Also: tune the high-volume subtypes. TARIFF_INCREASE_GOLD landed 17
  articles in one run — likely many are restatements of the same event.
  Consider deduplication by stripping outlet name from headline before
  hashing for idempotency check.

  ## V2 scorer calibration (Phase 2)

Observed during V2 first run (2026-06-20). Architecture works correctly;
score magnitudes need calibration with real forward-return data.

- **Multi-day media cycles inflate scores.** Dedup by (event_date, subtype)
  collapses same-day reports but not followup coverage. TITAN had 7
  distinct date-subtype pairs from one underlying gold duty hike event,
  producing score -0.389 when single-event signal would be ~-0.20.
  Phase 2: consider "first-mention within 30 days" semantics or
  semantic deduplication via NLP on article content.

- **Retrospective articles trigger as if events were fresh.** PLI_PHARMA
  article was a retrospective report on a years-old scheme but Mode B
  scored SUNPHARMA at +0.299 as if a new policy was announced. Pattern:
  classifier can't distinguish "X policy announced" from "X policy
  performance update years later". Phase 2: shorter lookback for
  news-detected events, or recency heuristics in classification.

- **Lookback window may be too wide.** Currently 180 days (widest decay
  window). For TARIFF events with 60-day decay, 180-day lookback catches
  old retrospective articles. Consider per-subtype lookback aligned to
  decay window.

- **Mode B sector fallback magnitudes need refinement.** Current 0.7
  discount across all inferred contributions is a single global heuristic.
  Real precision requires per-subtype-per-sector calibration.

- **Magnitudes in v2_policy_subtypes.csv are initial guesses.** Same
  pattern as V12 magnitudes — tune with forward-return data over 3-6
  months of live runs.

  ## V2 calibration items discovered during ship session (2026-06-22)

These were observed during V2 build/test cycle and accepted as Phase 2 work
rather than blocking the ship.

- **Mode B sector-inference coverage gap.** SUBTYPE_TO_SECTOR was narrowed
  to remove false positives (TEXTILES, ELECTRONICS, SOLAR, TELECOM,
  SEMICONDUCTORS placeholder mappings caused TITAN to wrongly score on
  PLI_TEXTILES events). As universe expands toward Nifty 100, re-add
  appropriate mappings: textile pure-plays (ARVIND/PAGEIND), telecom
  (BHARTIARTL), semiconductor (DIXON/MTAR if added).

- **Inferred-mapping overcoverage risk.** Explicit Mode A mappings can
  overstate stocks that are tangentially related to a policy category.
  Example caught during build: PIDILITIND was initially placed in
  ANTI_DUMPING_CHEMICALS but adhesives aren't typically the affected
  category in such rulings. Pattern to watch: stocks in broadly-named
  sectors (Chemicals, Consumer, FMCG) where policy events target specific
  sub-segments.

- **PLI_AUTO_COMPONENTS row is broad.** Currently maps 6 OEMs as
  beneficiaries of a component-PLI. OEMs are second-order beneficiaries
  (via cheaper components), not direct PLI recipients. May be more
  accurate to drop this Mode A row entirely and let Mode B sector
  inference handle it. Revisit when actual PLI_AUTO_COMPONENTS events
  trigger and we can observe magnitudes.

- **PRIVATIZATION_OIL sign is contested.** Currently -0.10 (bearish on
  competitive uncertainty). Alternative read: privatization improves
  operational discipline → re-rating positive (see PSU bank privatization
  history). When real PRIVATIZATION_OIL events fire, observe stock
  movement vs V2 prediction; flip sign if mismatch is consistent.

- **Score saturation from media-cycle duplication.** TITAN at -0.889
  during build was technically correct given 5 distinct news days of
  gold duty hike coverage, but a single underlying event reported across
  5 days produces saturation that misrepresents intent. Dedup-by-date
  helps for same-day duplicates only. Phase 3 work: semantic-level
  deduplication using NLP on article content.

  - **PLI_SEMICONDUCTORS overcoverage for DIXON.** DIXON is in the Mode A
  mapping for PLI_SEMICONDUCTORS at +0.30 magnitude. 3 events triggered
  PLI_SEMICONDUCTORS scoring in 24h, lifting DIXON's V2 contribution to
  saturation territory. Real-world position: DIXON has a semiconductor
  packaging JV initiative but isn't a primary semiconductor PLI recipient
  yet. Pattern: PLI subtype announcements fire mapping even when the
  specific announcement targets sector broadly rather than the mapped
  stock specifically. Phase 2: consider sub-categorization or event-level
  qualifier matching (e.g. "if event headline mentions DIXON by name,
  full magnitude; else half").

  ## V11 calibration (Phase 2)

Observed during V11 first run (2026-06-27). Architecture works correctly;
calibration items captured for future tuning.

- **Global lag window W=10d is a default, not calibrated.** Bhaiya's
  intuition: lag varies by pair. Hitachi/Siemens parent-sub track on
  slower cycles than Honda/Maruti. Phase 2: instrument residual
  distributions per pair, identify optimal W per (sub, parent), allow
  pair-specific override.

- **HYUNDAI β noisy.** Indian listing dates to Oct-2024 (~20 months
  history). β will stabilize over time. Track whether V11 contribution
  is dominated by noise vs signal in first 3-6 months.

- **BOSCHLTD parent_ticker NULL.** Robert Bosch GmbH is privately held,
  no public ticker. Phase 2 options: accept NO_SIGNAL, find a listed
  entity in the Bosch ownership chain, or use a Stoxx auto-supplier
  index as industry proxy. Defer until V11 has more live signal.

- **MOTHERSON uses parent-of-parent proxy.** global_parent is Sumitomo
  Wiring Systems (not listed); parent_ticker set to Sumitomo Electric
  (5802.T) which owns Sumitomo Wiring. β absorbs the imperfection but
  signal is one step removed. Assess whether 5802.T's diversified business
  (cables, autoparts, infocom) dilutes the parent-sub signal too much.

- **β-band confidence penalty (0.6 for |β| outside [0.2, 2.5])** is a
  heuristic with no empirical basis. Validate against forward returns
  and tune bounds over 3-6 months.

- **Currency normalization deferred.** Parent prices in local currency
  (EUR, JPY, KRW, USD, GBP, SGD, CHF). Percentage returns are
  currency-neutral so V11 v0 is unaffected. If V11 extends to absolute
  divergence signals later, currency layer needs adding.

## V11 ingestion follow-up

- **load_metadata.py does not read parent_ticker from stocks.csv.**
  Currently parent_ticker lives in DB only, populated via one-shot
  migration script `scripts/migrate_v11_parent_ticker.py`. Existing
  rows preserved across load_metadata runs (upsert by symbol) but new
  stocks added via CSV need manual migration. Phase 2: add parent_ticker
  column to stocks.csv + extend load_metadata to handle it. ~15min work.

## Data quality: NULL close values in PriceDaily (2026-06-27)

Surfaced during V11 first run. MARUTI, HINDUNILVR, NESTLEIND, ITC
crashed V11 with `float(None)` on close values. V11 patched to be
NULL-safe via `filter(PriceDaily.close.isnot(None))` and a
dict-comprehension guard, but the underlying data quality issue persists.

- Root cause unknown. Likely from earlier ingestion runs before
  consistent yfinance OHLC, or edge cases on ex-dividend / corporate
  action dates.
- Other scorers bulk-querying PriceDaily.close will hit the same trap
  unless they also filter NULLs.
- Phase 2 options: (a) NULL-filtering helper in data layer that all
  scorers inherit, (b) one-time cleanup pass to re-fetch and backfill
  NULL closes via yfinance, (c) add NOT NULL constraint at schema level
  (requires backfill first).
- Quick win: scan PriceDaily for NULL closes, identify (stock, date)
  pairs, re-fetch.


  ## V1 calibration items discovered during PIT ingester ship (2026-06-28)

Observed during PIT exploration + first ingestion run. Real ingester-side
issues that don't block the V1 ship but need handling.

- **Person category values are noisier than canonical SEBI taxonomy.**
  Actual values seen in first ingestion: `Promoters` (plural), `Promoter Group`,
  `Immediate relative` (mixed case), `Employees/Designated Employees`,
  `Other`, `-` (empty for some filings). Downstream code must use
  case-insensitive substring matching, not exact-match on canonical terms.

- **BAJFINANCE filing (pid=1197609) had unparseable transaction date.**
  Lost 1 of 15 real CONFLUX-universe filings in first run. Inspect raw
  payload — likely a fourth date format we haven't seen, or a genuinely
  empty date field. Fix: extend NSE_DATE_FORMATS, or fallback to
  intimation_date if acqfromDt/acqtoDt both missing.

- **`-` and empty transaction_type values appear in NSE PIT data.**
  1 of 15 had `-`. Downstream code must treat unparseable transaction_type
  as no-op rather than crash.

- **acq_mode taxonomy is broader than expected and includes routine modes
  alongside opportunistic ones.** Same NSE record may show `Market Purchase`,
  `Market Sale`, `Off Market`, `ESOP`, `Inter-se Transfer`, `Others`.
  Worth one-pass classification on raw data before scorer design.