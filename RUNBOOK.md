# CONFLUX v2 — Ship Runbook (₹0/month stack)

Everything below is free tier: **Neon** (Postgres) + **Render** (API) +
**Vercel** (frontend) + **GitHub Actions** (nightly cron, free on public repos).
Total monthly cost: **₹0**. Optional custom domain later: ~₹700–900/yr — not
needed to ship; Vercel gives you `https://conflux-<name>.vercel.app` with HTTPS.

Work top to bottom. Each phase ends with a verification gate — don't skip gates.

---

## Phase 0 — Drop in the kit (15 min)

1. Extract this zip **over** `D:\conflux` (it adds `backend/`, `frontend/`,
   `.github/`, new scripts; it REPLACES `data/schema.py` — the diff is only:
   env-driven `get_engine`, a `tier` column on Stock, `parent_ticker`
   length fix).
2. **Fix requirements.txt encoding** (it's UTF-16 from PowerShell `>`; pip on
   Linux rejects it — this would silently kill GitHub Actions):
   ```powershell
   (.venv) PS D:\conflux> python -m pip freeze | Out-File -Encoding utf8 requirements.txt
   ```
3. Add the one new pipeline dependency:
   ```powershell
   pip install "psycopg[binary]"
   python -m pip freeze | Out-File -Encoding utf8 requirements.txt
   ```
4. Sanity gate:
   ```powershell
   python -m scripts.run_daily 2>&1 | Select-Object -First 5
   ```
   Must start normally (SQLite fallback = zero behavior change). Ctrl+C once
   ingestion begins.
5. Commit: `feat(arch): ADR-003 kit — env-driven engine, tier column, bhavcopy, universe builder, api, frontend, nightly workflow`

## Phase 1 — Neon Postgres (20 min)

1. https://neon.tech → sign up (GitHub login) → New project → name `conflux`,
   region **AWS ap-southeast-1 (Singapore)** — closest to you and to NSE hours.
2. Copy the connection string. Convert it for SQLAlchemy: it must start with
   `postgresql+psycopg://` (replace `postgresql://`) and keep `?sslmode=require`.
3. In `D:\conflux\.env` add (this file is gitignored — verify with `git status`):
   ```
   CONFLUX_DATABASE_URL=postgresql+psycopg://<user>:<pass>@<host>/neondb?sslmode=require
   ```
4. Migrate your real DB (moat included — it's going to a private DB, not the repo):
   ```powershell
   python -m scripts.migrate_sqlite_to_postgres
   ```
   Gate: row counts printed per table match your local expectations, and
   "tagged tier=1" appears.
5. **Create the read-only API role** (Neon dashboard → SQL Editor):
   ```sql
   CREATE ROLE conflux_ro LOGIN PASSWORD '<generate-a-long-random-password>';
   GRANT CONNECT ON DATABASE neondb TO conflux_ro;
   GRANT USAGE ON SCHEMA public TO conflux_ro;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO conflux_ro;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO conflux_ro;
   ```
   The API gets a URL built with `conflux_ro` — even a fully compromised API
   process cannot write, update, or drop. The pipeline keeps the owner URL.

## Phase 2 — Expand the universe (30–60 min)

```powershell
python -m scripts.build_universe                    # dry run — read the counts
python -m scripts.build_universe --apply            # write (defaults: ₹30 floor, ₹0.5cr turnover)
```
Your 89 stay tier-1 curated, untouched. Expect roughly 1,400–1,800 tier-2 NSE
stocks post-filter plus the international watchlist. Tune `--price-floor` /
`--turnover-floor-cr` to taste and re-run — it's idempotent.

Then the first full-universe run (writes to Neon because `.env` is set):
```powershell
python -m scripts.run_daily_v2
```
Gate: log shows bhavcopy rows ≈ universe size, each scorer's `scored X/Y`,
and confluence written. Tier-1 stocks show all 7 vectors; tier-2 mostly V12/V13.
First run against Neon is the slowest (network round-trips); subsequent runs
are lighter. If bhavcopy 403s from your network, retry after a few minutes.

## Phase 3 — API on Render (30 min)

1. Push everything to GitHub first.
2. https://render.com → sign up with GitHub → **New → Blueprint** → pick the
   CONFLUX repo. It reads `render.yaml` and proposes `conflux-api` (free).
3. Set env vars when prompted:
   - `CONFLUX_DATABASE_URL` = the **conflux_ro** URL:
     `postgresql+psycopg://conflux_ro:<password>@<host>/neondb?sslmode=require`
   - `ALLOWED_ORIGINS` = `http://localhost:5173` for now (Vercel URL added in Phase 4)
4. Deploy. Gate: `https://conflux-api-xxxx.onrender.com/api/health` → `{"status":"ok"}`
   and `/api/rankings` returns rows. `/docs` must 404 (schema surface disabled — intended).
5. Security checks that should FAIL (that's the point):
   - Open `/api/stock/';DROP TABLE stocks--` → 422, and Neon still fine (it
     couldn't drop anyway: SELECT-only role).
   - Hammer refresh `/api/rankings` 60+ times in a minute → 429.

## Phase 4 — Frontend on Vercel (30 min)

Local test first:
```powershell
cd D:\conflux\frontend
npm install
# frontend/.env.local:  VITE_API_BASE=https://conflux-api-xxxx.onrender.com
npm run dev
```
Gate: rankings render, drill-down ledger opens, search works.

Deploy: https://vercel.com → sign up with GitHub → Add New Project → import
CONFLUX → **Root Directory = `frontend`** (critical) → framework auto-detects
Vite → add env var `VITE_API_BASE=https://conflux-api-xxxx.onrender.com` →
Deploy. You get `https://conflux-<name>.vercel.app`.

Close the CORS loop: Render → conflux-api → Environment →
`ALLOWED_ORIGINS=https://conflux-<name>.vercel.app` (add localhost too,
comma-separated, if you want local dev against prod API). Redeploy API.
Gate: the Vercel URL loads data; opening the API directly from another origin
via fetch is blocked by CORS.

**Custom domain?** Not needed — the Vercel subdomain is HTTPS, shareable, and
recruiter-fine. If you later buy `conflux.in`-style (~₹700/yr, the only
non-free item in this whole stack, and optional): Vercel → Domains → add →
one CNAME at your registrar, done in 10 minutes. Everything else unchanged.

## Phase 5 — Nightly automation (20 min)

1. GitHub repo → Settings → Secrets and variables → Actions → New secret:
   - `CONFLUX_DATABASE_URL` = the **owner** (writer) Neon URL
   - `FRED_API_KEY` = your existing key
2. The workflow (`.github/workflows/nightly.yml`) runs 18:45 IST weekdays.
   Trigger it once manually: Actions tab → nightly-refresh → Run workflow.
   Gate: green run; site shows the fresh date.
3. If NSE blocks the runner's IP repeatedly (known behavior with datacenter
   IPs): your fallback is one local command — `python -m scripts.run_daily_v2`
   — same DB, site updates identically. Automation is a convenience here, not
   a dependency.

## Phase 6 — Cutover & hygiene

- Keep conflux.streamlit.app alive until the new stack has run for a week,
  then point people at the Vercel URL (README + LinkedIn), and optionally
  retire Streamlit + R2.
- README: add the new URL, ADR-003 link, tier explanation.
- Rotate the Neon owner password if it ever touches a chat/screenshot.
- `.env`, `metadata/*.csv`, `data/*.db` remain gitignored — verify once:
  `git check-ignore .env metadata/stocks.csv data/conflux.db` prints all three.

## Security model (what "very tight" means here)

| Layer | Control |
|---|---|
| DB | Private Neon, TLS required, API uses SELECT-only role |
| API | GET-only, per-IP rate limits (60/min, 600/hr; 20/min drill-down), CORS allowlist, strict input validation, opaque 500s, no OpenAPI/docs surface, security headers, gzip+cache |
| Frontend | No secrets (public API base only), CSP, nosniff, frame-deny, HTTPS |
| Moat | Mapping/curation tables never serialized by any endpoint; CSVs/DB never in git |
| Pipeline | Secrets only in .env (local) / GitHub Secrets / Render env; failure-aware ingestion, ingestion_runs audit log |
| Legal | Persistent disclaimer: educational, not investment advice, not SEBI-registered |

## Known free-tier trade-offs (accepted, honest)

- Render sleeps after 15 min idle → ~50s first load (UI says "waking the engine").
- Neon free = 0.5 GB. At full universe with 400-day price history you may
  approach it in ~a year; prune old PriceDaily rows or trim history then.
- GitHub Actions runner IPs occasionally blocked by NSE → local fallback command.
