# CONFLUX

**Confluence Of Numerous Factors Linking Underlying eXposures**

A multi-vector confluence engine for the Indian equity market. Built on the framework
of 15 fundamental vectors that drive stock returns — input/output material costs,
government policy, promoter quality, supply/demand-side dynamics, macros, re-rating
catalysts, global parallels, and structural cycles.

The system ingests data daily, scores each Nifty 500 stock on each vector independently,
then aggregates into a composite *confluence score*. Top of the daily report = stocks
where 5+ vectors flash positive (or negative) simultaneously. Confluence is the signal.
Single vectors are not.

This is an **idea-generation engine**, not an auto-trader. Outputs go to a human (you).

## Architecture (four layers)

1. **Stock Metadata Graph** — hand-curated facts per stock (sectors, input commodities,
   customers, suppliers, global parents, promoter groups, peers). This is the moat.
2. **Data Ingestion Pipelines** — scheduled jobs that fetch prices, commodities, FX,
   rates, corporate announcements, policy news, IPO calendar.
3. **Vector Scorers** — one module per vector. Each outputs -1.0 to +1.0 per stock per day.
4. **Confluence Engine + Dashboard** — aggregates active vectors, ranks setups, Streamlit UI.

## Status

**Phase 1 (in progress):** Foundation + V4 (input material cost) + V13 (macros).
Nifty 500 universe. Metadata graph populated in parallel via Google Sheet.

## Roadmap

| Phase | Vectors                      | Status        |
|-------|------------------------------|---------------|
| 1     | V4, V13                      | in progress   |
| 2     | V2, V12                      | planned       |
| 3     | V1, V7, V11                  | planned       |
| 4     | V8, V10, V14, V15 (LLM-based)| planned       |

## The 15 Vectors

1. Promoters (political ties, board changes, scams, pledging)
2. Government policies (PLI, anti-dumping, duties, budget)
3. Holding companies (subsidiary IPOs, hidden assets)
4. Input material cost ← Phase 1
5. Output material cost
6. Input material supply side
7. Output product demand side
8. Services companies (recruitment, app downloads, ad-conversion)
9. Global capex focus (solar, defence, AI, EV, nuclear, space)
10. User behaviour shifts
11. Global parallels (parent → Indian subsidiary)
12. Re-rating scenarios
13. Geopolitics & macros ← Phase 1
14. Structural up/down cycles
15. Moat / pricing power / capital efficiency

## Repo structure

```
conflux/
├── data/              # SQLite DB + schema
├── ingestion/         # one script per data source
├── scorers/           # one script per vector
├── confluence/        # aggregation engine
├── app/               # Streamlit dashboard
├── metadata/          # hand-curated CSVs
├── scripts/           # orchestration utilities
├── tests/             # unit tests
└── docs/              # design decisions
```

## Related projects

- **carma-correlator-v1** — legacy commodity correlator, manual lookup tool
- **CARMA** — Calibrated Adaptive Regime Market Architecture (separate project)
