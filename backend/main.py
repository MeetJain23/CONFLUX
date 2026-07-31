"""
CONFLUX public API — read-only, rate-limited, moat-safe.

Security model (defense in depth):
  1. READ-ONLY BY CONSTRUCTION: only GET endpoints exist; the DB user should be
     a SELECT-only role (see RUNBOOK — `conflux_ro` grants). Even full app
     compromise cannot write or drop.
  2. MOAT BOUNDARY: responses expose scores, confidence, direction, and
     rationale strings only. stock_input_commodities, policy/supply mappings,
     customers/suppliers, promoter_group — never serialized, no endpoint
     touches those tables.
  3. RATE LIMITS: per-IP via slowapi. 60/min general, 20/min drill-down,
     600/hour hard ceiling. Behind Render's proxy we trust X-Forwarded-For's
     first hop only.
  4. INPUT VALIDATION: symbols must match ^[A-Z0-9&._-]{1,25}$; dates are ISO
     parsed or rejected; limits clamped. Everything else is 422 before any DB
     touch. ORM parameterization kills injection.
  5. CORS ALLOWLIST from env (your Vercel URL + localhost). No wildcard.
  6. HEADERS: nosniff, frame-deny, referrer-policy, no-store on errors,
     Cache-Control public max-age=300 on data (nightly data = cache freely).
  7. NO SECRETS CLIENT-SIDE: DATABASE_URL lives only in Render env vars.
  8. OPAQUE ERRORS: any unhandled exception returns a generic 500; details go
     to server logs only.

Env vars:
  CONFLUX_DATABASE_URL   postgresql+psycopg://conflux_ro:...@.../conflux?sslmode=require
  ALLOWED_ORIGINS        comma-separated, e.g. https://conflux.vercel.app,http://localhost:5173
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date as date_type

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func

# Import the existing CONFLUX models — backend/ is deployed with the repo root
# on PYTHONPATH (see render.yaml rootDir + startCommand).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.schema import Stock, ConfluenceScore, VectorScore, get_session  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("conflux.api")

SYMBOL_RE = re.compile(r"^[A-Z0-9&._-]{1,25}$")
VECTOR_NAMES = {
    1: "Promoters & insider activity", 2: "Government policy",
    3: "Holding companies", 4: "Input material cost", 5: "Output material cost",
    6: "Input supply side", 7: "Output demand side", 8: "Services signals",
    9: "Global capex focus", 10: "User behaviour shifts", 11: "Global parallels",
    12: "Re-rating catalysts", 13: "Geopolitics & macros",
    14: "Structural cycles", 15: "Moat & capital efficiency",
}

limiter = Limiter(key_func=get_remote_address,
                  default_limits=["600/hour", "60/minute"],
                  headers_enabled=True)

app = FastAPI(title="CONFLUX API", version="2.0",
              docs_url=None, redoc_url=None, openapi_url=None)  # no public schema surface
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(GZipMiddleware, minimum_size=1024)

_origins = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["GET"], allow_headers=["*"], max_age=86400)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.url.path.startswith("/api/") and resp.status_code == 200:
        resp.headers.setdefault("Cache-Control", "public, max-age=300")
    return resp


@app.exception_handler(Exception)
async def opaque_errors(request: Request, exc: Exception):
    logger.exception(f"unhandled on {request.url.path}: {exc}")
    return JSONResponse(status_code=500,
                        content={"detail": "internal error"},
                        headers={"Cache-Control": "no-store"})


def _session():
    return get_session()


def _parse_date(s: str | None) -> date_type | None:
    if s is None:
        return None
    try:
        return date_type.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")


def _latest_date(session) -> date_type | None:
    return session.query(func.max(ConfluenceScore.date)).scalar()


def _breakdown(raw: str | None) -> list[dict]:
    """Serialize the glass-box breakdown. Scores + rationale only — never mapping inputs."""
    if not raw:
        return []
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out = []
    for vid, v in sorted(d.items(), key=lambda kv: int(kv[0])):
        out.append({"vector_id": int(vid),
                    "vector_name": VECTOR_NAMES.get(int(vid), f"V{vid}"),
                    "score": v.get("score"),
                    "confidence": v.get("confidence"),
                    "rationale": v.get("rationale")})
    return out


@app.get("/api/health")
@limiter.exempt
def health():
    return {"status": "ok"}


@app.get("/api/meta")
@limiter.limit("30/minute")
def meta(request: Request):
    s = _session()
    try:
        latest = _latest_date(s)
        n_active = s.query(Stock).filter(Stock.active.is_(True)).count()
        n_t1 = s.query(Stock).filter(Stock.active.is_(True), Stock.tier == 1).count()
        sectors = [r[0] for r in s.query(Stock.sector).filter(
            Stock.active.is_(True), Stock.sector.isnot(None))
            .distinct().order_by(Stock.sector).all()]
        n_scored = 0
        if latest:
            n_scored = s.query(ConfluenceScore).filter(ConfluenceScore.date == latest).count()
        return {"latest_date": latest.isoformat() if latest else None,
                "universe_active": n_active, "tier1_curated": n_t1,
                "scored_on_latest": n_scored, "sectors": sectors,
                "vectors": [{"id": k, "name": v} for k, v in VECTOR_NAMES.items()],
                "disclaimer": ("Educational research tool. Not investment advice. "
                               "Not a SEBI-registered investment adviser.")}
    finally:
        s.close()


@app.get("/api/rankings")
@limiter.limit("60/minute")
def rankings(request: Request,
             date: str | None = None,
             sector: str | None = Query(None, max_length=100),
             tier: int | None = Query(None, ge=1, le=2),
             direction: str | None = Query(None, pattern="^(bullish|bearish|neutral)$"),
             sort: str = Query("desc", pattern="^(asc|desc|abs)$"),
             limit: int = Query(50, ge=1, le=200),
             offset: int = Query(0, ge=0, le=5000)):
    s = _session()
    try:
        asof = _parse_date(date) or _latest_date(s)
        if asof is None:
            return {"date": None, "total": 0, "rows": []}

        q = (s.query(ConfluenceScore, Stock)
             .join(Stock, Stock.id == ConfluenceScore.stock_id)
             .filter(ConfluenceScore.date == asof, Stock.active.is_(True)))
        if sector:
            q = q.filter(Stock.sector == sector)
        if tier:
            q = q.filter(Stock.tier == tier)
        if direction:
            q = q.filter(ConfluenceScore.direction == direction)

        total = q.count()
        if sort == "asc":
            q = q.order_by(ConfluenceScore.composite.asc())
        elif sort == "abs":
            q = q.order_by(func.abs(ConfluenceScore.composite).desc())
        else:
            q = q.order_by(ConfluenceScore.composite.desc())

        rows = [{"symbol": st.symbol_nse, "name": st.name, "sector": st.sector,
                 "tier": st.tier, "composite": round(c.composite, 3),
                 "direction": c.direction, "n_pos": c.n_vectors_positive,
                 "n_neg": c.n_vectors_negative, "n_active": c.n_vectors_active}
                for c, st in q.limit(limit).offset(offset).all()]
        return {"date": asof.isoformat(), "total": total, "rows": rows}
    finally:
        s.close()


@app.get("/api/stock/{symbol}")
@limiter.limit("20/minute")
def stock_detail(request: Request, symbol: str, date: str | None = None):
    symbol = symbol.upper().strip()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(422, "invalid symbol")
    s = _session()
    try:
        st = s.query(Stock).filter(Stock.symbol_nse == symbol).first()
        if st is None:
            raise HTTPException(404, "unknown symbol")
        asof = _parse_date(date) or _latest_date(s)

        latest = (s.query(ConfluenceScore)
                  .filter(ConfluenceScore.stock_id == st.id,
                          ConfluenceScore.date <= (asof or date_type.today()))
                  .order_by(ConfluenceScore.date.desc()).first())

        history = (s.query(ConfluenceScore.date, ConfluenceScore.composite,
                           ConfluenceScore.direction)
                   .filter(ConfluenceScore.stock_id == st.id)
                   .order_by(ConfluenceScore.date.desc()).limit(90).all())

        return {"symbol": st.symbol_nse, "name": st.name, "sector": st.sector,
                "sub_sector": st.sub_sector, "tier": st.tier,
                "international": st.symbol_nse.endswith("_INTL"),
                "latest": None if latest is None else {
                    "date": latest.date.isoformat(),
                    "composite": round(latest.composite, 3),
                    "direction": latest.direction,
                    "n_pos": latest.n_vectors_positive,
                    "n_neg": latest.n_vectors_negative,
                    "n_active": latest.n_vectors_active,
                    "vectors": _breakdown(latest.vector_breakdown_json)},
                "history": [{"date": d.isoformat(), "composite": round(c, 3),
                             "direction": dr} for d, c, dr in reversed(history)]}
    finally:
        s.close()


@app.get("/api/search")
@limiter.limit("30/minute")
def search(request: Request, q: str = Query(..., min_length=1, max_length=40)):
    term = re.sub(r"[^A-Za-z0-9&._ -]", "", q).strip()
    if not term:
        return {"rows": []}
    s = _session()
    try:
        like = f"%{term.upper()}%"
        rows = (s.query(Stock)
                .filter(Stock.active.is_(True))
                .filter((func.upper(Stock.symbol_nse).like(like)) |
                        (func.upper(Stock.name).like(like)))
                .order_by(Stock.tier.asc(), Stock.symbol_nse.asc())
                .limit(15).all())
        return {"rows": [{"symbol": r.symbol_nse, "name": r.name,
                          "sector": r.sector, "tier": r.tier} for r in rows]}
    finally:
        s.close()
