# ADR-003: Expanded universe — Postgres, tiered coverage, API + React frontend

**Status:** Accepted
**Date:** Jul 2026

## Context
CONFLUX v1: ~89 curated stocks, SQLite, Streamlit Cloud + private R2. Goal:
(nearly) all NSE equities ex-penny plus an international watchlist, served
publicly with a proper backend/frontend, at ~zero cost, with tight security.

ADR-001 named its own migration triggers — multi-user dashboard OR ~10M rows.
A public API over ~2,000 stocks × 7 vectors × daily history hits both.

## Decisions
1. **Postgres (Neon free tier) via env var.** `get_engine()` honors
   `CONFLUX_DATABASE_URL`; unset = local SQLite. One code path, two targets.
   SQLAlchemy models unchanged — the escape hatch ADR-001 promised.
2. **Tiered universe.** The moat cannot be hand-curated at 2,000 stocks in any
   honest timeframe. Tier 1 = curated (all vectors). Tier 2 = automated
   vectors only. Confidence-weighting already handles partial coverage; the
   tier is surfaced in API and UI. Coverage honesty is the glass-box principle
   applied to the universe itself.
3. **Bhavcopy over yfinance for NSE prices.** One official EOD download for
   the whole market vs ~2,000 rate-limited calls. yfinance remains for
   international watchlist, commodities, macros.
4. **Read-only FastAPI perimeter.** GET-only endpoints, SELECT-only DB role,
   per-IP rate limits, CORS allowlist, strict input validation, opaque errors,
   no OpenAPI surface. Moat tables (input commodities, mappings, customers,
   suppliers, promoter groups) are never serialized.
5. **React (Vite) on Vercel free; API on Render free; nightly refresh via
   GitHub Actions (free on public repos), with local `run_daily_v2` as the
   fallback runner writing to the same Postgres.**
6. **Streamlit + R2 stays live during transition**, retired only after the new
   stack is verified.

## Consequences
- Total cost: ₹0/month. Trade-off accepted: Render free tier sleeps when idle
  (~50s cold start, surfaced honestly in the UI).
- Nightly writes from home IP or Actions runner; NSE datacenter-IP blocking is
  mitigated by the archives host + local fallback.
- Two deployables (api, frontend) + one workflow to maintain.
