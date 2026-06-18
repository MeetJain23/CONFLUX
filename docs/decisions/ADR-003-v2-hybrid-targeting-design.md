# ADR-003: V2 (Government Policy) — Hybrid Stock-Targeting Design



**Status:** Accepted (2026-06-15)

**Decision context:** Designing V2 (Vector 2 — Government Policy) for Phase 2

implementation. Question: how does V2 determine which stocks are affected by

a given policy event?



## Context



V2 scores stocks based on discrete government policy events: PLI schemes,

anti-dumping rulings, tariff changes, budget allocations, GST changes,

privatization, sector subsidies.



Unlike V12 (which uses sector-level peer scoping for corporate events) and

V13 (which scores entirely at sector level), policy events have inherently

varied stock-level impact. A "PLI for semiconductors" announcement affects

2-3 specific companies. A "tariff on imported steel" affects every stock

that uses steel as an input. A "budget allocation to defence" affects the

defence sector broadly.



A single targeting strategy doesn't fit all policy event types.



## Decision



V2 will use a **two-mode hybrid stock-targeting strategy**:



### Mode A — Explicit Mapping (highest precision)



For policies with known, specific beneficiaries:



- `metadata/v2\_policy\_mappings.csv` — gitignored, curated, the moat

- Each row: `policy\_subtype, affected\_stocks, base\_magnitude, decay\_days, notes`

- When event matches a row, use that row's stocks and magnitudes exactly

- Example: `PLI\_SEMICONDUCTORS → \[DIXON, MTAR] at magnitude 0.40`



This mode encodes the user's investment judgment — which specific companies

benefit from which specific policies. Same nature as `stock\_input\_commodities`

metadata: hand-curated, high-value, protected.



### Mode B — Inferred Mapping (automated via existing metadata)



For policies that map cleanly to existing CONFLUX metadata structures:



- `TARIFF_*` and `DUTY_*` subtypes → query `stock\_input\_commodities` table

&#x20; for stocks with the affected commodity as an input. Magnitude scales by

&#x20; the stock's commodity weight.

\- `BUDGET\_\*` and `PLI\_\*` subtypes (when no explicit mapping exists) →

&#x20; query stocks by sector tag from `stocks` table.

\- Inferred magnitudes apply a \*\*0.7 discount factor\*\* vs explicit magnitudes

&#x20; to reflect lower confidence in automatically-targeted impact.



This mode reuses existing CONFLUX metadata (the input-commodities graph,

sector tags) for stock targeting, dramatically reducing maintenance burden

for common policy categories.



\### Mode C — Unmapped (logged, not scored)



When neither explicit nor inferred targeting applies:



\- Event ingests successfully (preserves history)

\- Logged at WARNING level with subtype for future curation review

\- Returns no score for any stock



\## Why this design



Three approaches were considered:



\*\*Approach 1: Pure explicit mapping (rejected)\*\*

\- Highest precision, but unbounded maintenance burden

\- Every novel policy subtype requires manual mapping before V2 sees signal

\- Risk: V2 silently misses new event types until you notice and curate



\*\*Approach 2: Pure sector-level scoring (rejected)\*\*

\- Lowest maintenance, but reintroduces V13's sector-uniformity weakness

\- All policy events become "sector got a boost" — loses precision that

&#x20; defines V2's value proposition

\- A PLI for 2 specific semiconductor stocks would lift the entire Auto

&#x20; sector uniformly, which is wrong



\*\*Approach 3: Hybrid (accepted)\*\*

\- Combines explicit precision where curated with automated coverage for

&#x20; the residual long tail

\- Reuses existing metadata graph (input\_commodities, sectors) rather than

&#x20; duplicating

\- Honest about uncertainty via the 0.7 discount factor on inferred mode

\- Glass-box throughout — every score traceable to either an explicit row

&#x20; or an inferred metadata path



\## Trade-offs accepted



1\. \*\*Inferred magnitudes are crude.\*\* A tariff change isn't necessarily a

&#x20;  0.30 boost regardless of size or duration. Phase 2 calibration work.



2\. \*\*First-of-kind policies still need curation.\*\* Government invents a

&#x20;  new scheme type → no automation helps. Accepted as residual burden.



3\. \*\*Mode A vs Mode B precedence matters.\*\* A subtype that has BOTH an

&#x20;  explicit row AND an inferred path should use the explicit row.

&#x20;  The scorer checks Mode A first, falls through to Mode B only if no

&#x20;  explicit row matches.



4\. \*\*Negative events supported.\*\* V2 is bidirectional. Tariff cuts that

&#x20;  hurt domestic producers get negative magnitudes in their mapping rows.



\## Front-loaded curation requirement



Before V2 ships, the user commits to a pre-seeding session: 2-3 hours

populating `v2\_policy\_mappings.csv` with explicit mappings for the

20-40 most likely policy subtypes. This concentrates curation work

into a single session rather than spreading it as ongoing weekly burden.



\## Implementation surface



New files:



\- `data/schema.py` — add `PolicyEvent` table (similar shape to `CorporateAction`)

\- `metadata/v2\_policy\_subtypes.csv` — tracked, config. Magnitudes and

&#x20; decay windows per subtype, parallel to `v12\_event\_magnitudes.csv`

\- `metadata/v2\_policy\_mappings.csv` — gitignored, moat. Explicit

&#x20; per-subtype stock lists.

\- `ingestion/policy\_news.py` — PIB RSS + Google News RSS ingester with

&#x20; curated keyword set, regex-based classification

\- `scorers/v02\_govt\_policy.py` — scorer implementing the Mode A / B / C

&#x20; targeting logic above



Modified files:



\- `scripts/run\_daily.py` — wire V2 into orchestrator

\- `app/dashboard.py` — no changes required (drill-down auto-supports new vectors)



Estimated effort: 6-8 hours focused work across 2 sessions.



\## Phase 2 calibration items



\- Magnitudes in `v2\_policy\_subtypes.csv` start as initial guesses, need

&#x20; 3-6 months of forward-return data to tune.

\- The 0.7 inferred-mode discount is an unvalidated heuristic. Tune with

&#x20; observed performance.

\- Consider sub-sector granularity in Mode B (current design uses sector

&#x20; level; sub-sector might improve precision for inferred events).

\- Open question: should explicit and inferred contributions for the same

&#x20; stock-event combine, or should explicit override inferred entirely?

&#x20; Currently the design says explicit overrides; revisit if real events

&#x20; suggest otherwise.



\## References



\- Design conversation: 2026-06-15 evening session

\- Related: ADR-001 (SQLite over Postgres), ADR-002 (Vector score range)

\- Related: TODO.md "V12 magnitude calibration" — same calibration pattern

&#x20; will apply to V2 magnitudes once shipped

