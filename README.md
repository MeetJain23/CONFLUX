# CONFLUX

**A multi-vector confluence engine for Indian equities.**

Live: [conflux-amber.vercel.app](https://conflux-amber.vercel.app) · Educational research tool, not investment advice.

---

## The idea

Most equity analysis fixates on a single signal: one chart, one ratio, one headline. But markets rarely move on one thing. They move when several *independent* forces line up in the same direction at the same time.

CONFLUX scores roughly 1,480 NSE-listed stocks across independent fundamental vectors, then surfaces the names where those signals **converge**. One vector is noise. Agreement across vectors is signal.

Every score is a **glass box**: it decomposes into a per-vector ledger showing each vector's score, its confidence weight, and a plain-language rationale. When a data feed is stale, the engine **withholds** that vector's score rather than fabricating one.

## What it does

- Scores ~1,480 stocks across a tiered universe (curated + automated coverage)
- Composite score is a **confidence-weighted, breadth-adjusted** blend of active vectors, so a single strong vector cannot outrank genuine multi-vector agreement (see `docs/decisions/ADR-004`)
- Full drill-down: click any stock to open its vector ledger with evidence and confidence per vector
- Honest coverage: unmapped or stale vectors are shown as withheld, never faked

**Example.** ONGC surfaced bullish because a supply-side vector caught Strait of Hormuz disruptions *and* a macro vector turned positive on oil, at the same time. Two independent signals agreeing.

## Vectors

7 of a planned 15 vectors are currently live:

| # | Vector | Status |
|---|--------|--------|
| V1 | Promoters & insider activity | live |
| V2 | Government policy | live |
| V4 | Input material cost | live |
| V6 | Input supply side | live |
| V11 | Global parallels | live |
| V12 | Re-rating catalysts | live |
| V13 | Geopolitics & macros | live |
| V3, V5, V7–V10, V14, V15 | - | planned |

## Architecture

Built end to end as real infrastructure, not a notebook, and running entirely on free-tier services.

```
NSE bhavcopy / corporate actions / news  ┐
FRED macro series                        ├─►  ingestion  ─►  scoring engine  ─►  local SQLite
curated moat (CSV mappings, gitignored)  ┘                                            │
                                                              nightly: score locally  │
                                                              then sync results only  ▼
                                          React (Vercel)  ◄─  FastAPI (Render)  ◄─  PostgreSQL (Neon)
                                                                read-only, rate-limited
```

**Design decisions**

- **Score locally, publish results.** The scoring engine runs on the machine that holds the curated moat (never committed to git). Only derived scores sync to the cloud database, so the moat never leaves local infrastructure. See `docs/decisions/ADR-003`.
- **Full-market prices via NSE bhavcopy**, not per-ticker API loops, one daily download covers the universe.
- **Read-only API perimeter.** The public API connects to Postgres through a `SELECT`-only role, is rate-limited per IP, validates all input, locks CORS to the frontend origin, and never serializes the moat tables.
- **Tiered universe.** Hand-curated stocks carry all vectors; the broader automated tier carries the subset that computes without hand-mapping, at honest confidence.

## Stack

Python · SQLAlchemy · FastAPI · PostgreSQL (Neon) · React · Vite · deployed across Neon, Render, and Vercel.

## Architecture decision records

Real decisions, documented as they were made, in `docs/decisions/`:

- **ADR-001** — SQLite as the initial store, with explicit migration triggers
- **ADR-003** — Expanded universe: Postgres, tiered coverage, API + frontend, score-locally-sync-results
- **ADR-004** — Breadth-weighted composite: rewarding multi-vector agreement

## Status & disclaimer

CONFLUX is an **educational research tool**. It is **not investment advice** and **not** the output of a SEBI-registered investment adviser. 7 of 15 vectors are live; coverage and methodology are actively evolving.

## The 15 Vectors

1. Promoters (political ties, board changes, scams, pledging) ← Phase 3
2. Government policies (PLI, anti-dumping, duties, budget) ← Phase 2
3. Holding companies (subsidiary IPOs, hidden assets)
4. Input material cost ← Phase 1
5. Output material cost
6. Input material supply side ← Phase 3
7. Output product demand side
8. Services companies (recruitment, app downloads, ad-conversion)
9. Global capex focus (solar, defence, AI, EV, nuclear, space)
10. User behaviour shifts
11. Global parallels (parent → Indian subsidiary) ← Phase 3
12. Re-rating scenarios ← Phase 2
13. Geopolitics & macros ← Phase 1
14. Structural up/down cycles
15. Moat / pricing power / capital efficiency

## Repo structure