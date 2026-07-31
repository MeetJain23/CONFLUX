import React, { useEffect, useMemo, useRef, useState } from 'react'
import { fetchMeta, fetchRankings, fetchStock, searchStocks } from './api.js'

/* ---------- shared bits ---------- */

const fmt = (x) => (x === null || x === undefined ? '—' : (x > 0 ? '+' : '') + x.toFixed(3))

function DivergingBar({ value, confidence = 1, max = 1, height = 8 }) {
  // The signature mark: a bar off a center spine. Length = score,
  // opacity = confidence — the confluence weighting, drawn.
  const pct = Math.min(Math.abs(value) / max, 1) * 50
  const pos = value >= 0
  return (
    <div className="dbar" style={{ height }} aria-hidden="true">
      <div className="dbar-spine" />
      <div
        className={`dbar-fill ${pos ? 'pos' : 'neg'}`}
        style={{
          width: `${pct}%`,
          [pos ? 'left' : 'right']: '50%',
          opacity: 0.35 + 0.65 * Math.max(0, Math.min(confidence, 1)),
        }}
      />
    </div>
  )
}

function TierMark({ tier }) {
  return tier === 1
    ? <span className="tier1" title="Tier 1 — hand-curated: all live vectors apply">curated</span>
    : <span className="tier2" title="Tier 2 — automated coverage only">auto</span>
}

/* ---------- drill-down: the vector ledger ---------- */

function StockPanel({ symbol, onClose }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    setData(null); setErr(null)
    fetchStock(symbol).then(setData).catch((e) => setErr(e.message))
  }, [symbol])
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="panel-scrim" onClick={onClose}>
      <aside className="panel" onClick={(e) => e.stopPropagation()} aria-label={`${symbol} detail`}>
        <button className="panel-close" onClick={onClose} aria-label="Close">×</button>
        {err === 'rate' && <p className="note">Rate limit reached — wait a minute and retry.</p>}
        {err && err !== 'rate' && <p className="note">Couldn't load {symbol}. Retry shortly.</p>}
        {!data && !err && <p className="note">Loading ledger…</p>}
        {data && (
          <>
            <header className="panel-head">
              <h2>{data.symbol.replace('_INTL', '')}</h2>
              <p className="muted">{data.name}{data.sector ? ` · ${data.sector}` : ''} <TierMark tier={data.tier} /></p>
            </header>

            {data.latest ? (
              <>
                <div className="composite-block">
                  <div className="composite-row">
                    <span className={`big mono ${data.latest.composite >= 0 ? 'c-pos' : 'c-neg'}`}>
                      {fmt(data.latest.composite)}
                    </span>
                    <span className={`dir dir-${data.latest.direction}`}>{data.latest.direction}</span>
                  </div>
                  <p className="muted small">
                    {data.latest.n_active} vectors active · {data.latest.n_pos} positive · {data.latest.n_neg} negative · as of {data.latest.date}
                  </p>
                </div>

                <h3 className="ledger-title">Vector ledger</h3>
                <p className="muted small">Bar length is the score; bar opacity is the confidence that weights it. Nothing hidden.</p>
                <ol className="ledger">
                  {data.latest.vectors.map((v) => (
                    <li key={v.vector_id} className="ledger-row">
                      <div className="ledger-top">
                        <span className="vname">V{v.vector_id} · {v.vector_name}</span>
                        <span className="mono vnum">{fmt(v.score)} <em className="muted">×{(v.confidence ?? 1).toFixed(2)}</em></span>
                      </div>
                      <DivergingBar value={v.score ?? 0} confidence={v.confidence ?? 1} />
                      {v.rationale && <p className="rationale">{v.rationale}</p>}
                    </li>
                  ))}
                </ol>

                {data.history.length > 1 && (
                  <>
                    <h3 className="ledger-title">Composite, last {data.history.length} sessions</h3>
                    <div className="spark" role="img" aria-label="composite history">
                      {data.history.map((h) => (
                        <div key={h.date} className={`spark-col ${h.composite >= 0 ? 'pos' : 'neg'}`}
                          style={{ height: `${Math.min(Math.abs(h.composite), 1) * 100}%` }}
                          title={`${h.date}: ${fmt(h.composite)}`} />
                      ))}
                    </div>
                  </>
                )}
              </>
            ) : (
              <p className="note">No confluence score yet for this stock — likely a tier-2 name whose applicable vectors haven't fired. Absence is honest here, not an error.</p>
            )}
          </>
        )}
      </aside>
    </div>
  )
}

/* ---------- main ---------- */

export default function App() {
  const [meta, setMeta] = useState(null)
  const [waking, setWaking] = useState(false)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState({ sector: '', tier: '', direction: '', sort: 'desc' })
  const [selected, setSelected] = useState(null)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState([])
  const [error, setError] = useState(null)
  const searchTimer = useRef(null)
  const LIMIT = 50

  useEffect(() => {
    const t = setTimeout(() => setWaking(true), 2500) // free-tier cold start notice
    fetchMeta().then((m) => { setMeta(m); setWaking(false); clearTimeout(t) })
      .catch(() => { setError('api'); setWaking(false) })
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    setError(null)
    fetchRankings({ ...filters, limit: LIMIT, offset: page * LIMIT })
      .then((r) => { setRows(r.rows); setTotal(r.total) })
      .catch((e) => setError(e.message === 'rate' ? 'rate' : 'api'))
  }, [filters, page, meta])

  useEffect(() => {
    clearTimeout(searchTimer.current)
    if (q.trim().length < 2) { setHits([]); return }
    searchTimer.current = setTimeout(() => {
      searchStocks(q.trim()).then((r) => setHits(r.rows)).catch(() => setHits([]))
    }, 300)
  }, [q])

  const maxAbs = useMemo(
    () => Math.max(0.3, ...rows.map((r) => Math.abs(r.composite))), [rows])

  const setF = (k) => (e) => { setPage(0); setFilters((f) => ({ ...f, [k]: e.target.value })) }

  return (
    <div className="wrap">
      <header className="masthead">
        <div>
          <h1 className="wordmark">CONFLUX</h1>
          <p className="tagline">Single vectors are noise. Confluence is the signal.</p>
        </div>
        <div className="meta mono">
          {meta && <>
            <span>{meta.latest_date ?? '—'}</span>
            <span>{meta.universe_active} stocks · {meta.tier1_curated} curated</span>
            <span>{meta.scored_on_latest} scored today</span>
          </>}
        </div>
      </header>

      {waking && !meta && !error && (
        <p className="note">Waking the engine — the free-tier server sleeps when idle. First load can take up to a minute; every load after is instant.</p>
      )}
      {error === 'api' && <p className="note">The API isn't reachable right now. If this is a cold start, give it a minute and reload.</p>}
      {error === 'rate' && <p className="note">Rate limit reached — this instance is protected against heavy pulls. Wait a minute.</p>}

      <section className="filters" aria-label="Filters">
        <div className="searchbox">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbol or name"
            aria-label="Search stocks" />
          {hits.length > 0 && (
            <ul className="hits">
              {hits.map((h) => (
                <li key={h.symbol}>
                  <button onClick={() => { setSelected(h.symbol); setQ(''); setHits([]) }}>
                    <span className="mono">{h.symbol.replace('_INTL', '')}</span> {h.name} <TierMark tier={h.tier} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <select value={filters.sector} onChange={setF('sector')} aria-label="Sector">
          <option value="">All sectors</option>
          {meta?.sectors.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={filters.tier} onChange={setF('tier')} aria-label="Tier">
          <option value="">All tiers</option>
          <option value="1">Tier 1 — curated</option>
          <option value="2">Tier 2 — automated</option>
        </select>
        <select value={filters.direction} onChange={setF('direction')} aria-label="Direction">
          <option value="">All directions</option>
          <option value="bullish">Bullish</option>
          <option value="bearish">Bearish</option>
          <option value="neutral">Neutral</option>
        </select>
        <select value={filters.sort} onChange={setF('sort')} aria-label="Sort">
          <option value="desc">Most bullish first</option>
          <option value="asc">Most bearish first</option>
          <option value="abs">Strongest signal first</option>
        </select>
      </section>

      <section aria-label="Rankings">
        <table className="ranks">
          <thead>
            <tr>
              <th className="num">#</th><th>Stock</th><th className="hide-sm">Sector</th>
              <th>Composite</th><th className="bar-col" aria-hidden="true"></th>
              <th className="hide-sm">Vectors +/−</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.symbol} onClick={() => setSelected(r.symbol)} tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setSelected(r.symbol)}>
                <td className="num mono muted">{page * LIMIT + i + 1}</td>
                <td>
                  <span className="mono sym">{r.symbol.replace('_INTL', '')}</span>
                  <span className="cname">{r.name}</span> <TierMark tier={r.tier} />
                </td>
                <td className="hide-sm muted">{r.sector ?? '—'}</td>
                <td className={`mono ${r.composite >= 0 ? 'c-pos' : 'c-neg'}`}>{fmt(r.composite)}</td>
                <td className="bar-col"><DivergingBar value={r.composite} max={maxAbs} /></td>
                <td className="hide-sm mono muted">{r.n_pos}↑ {r.n_neg}↓ / {r.n_active}</td>
              </tr>
            ))}
            {rows.length === 0 && !error && (
              <tr><td colSpan="6" className="note">No scores for this filter yet. The engine writes after each market close.</td></tr>
            )}
          </tbody>
        </table>
        {total > LIMIT && (
          <div className="pager">
            <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Newer ranks</button>
            <span className="mono muted">{page * LIMIT + 1}–{Math.min((page + 1) * LIMIT, total)} of {total}</span>
            <button disabled={(page + 1) * LIMIT >= total} onClick={() => setPage((p) => p + 1)}>Older ranks</button>
          </div>
        )}
      </section>

      {selected && <StockPanel symbol={selected} onClose={() => setSelected(null)} />}

      <footer className="foot">
        <p>{meta?.disclaimer ?? 'Educational research tool. Not investment advice. Not a SEBI-registered investment adviser.'}</p>
        <p className="muted">Glass-box by design — every score opens into the exact inputs that produced it. Tier-2 stocks carry automated vectors only; their thinner coverage is shown, never faked.</p>
      </footer>
    </div>
  )
}
