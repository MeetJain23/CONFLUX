// Thin API client. No keys, no secrets — the backend is public read-only
// and rate-limited server-side; nothing sensitive can live here by design.
const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function get(path, params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''))
  ).toString()
  const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`)
  if (res.status === 429) throw new Error('rate')
  if (!res.ok) throw new Error(`http ${res.status}`)
  return res.json()
}

export const fetchMeta = () => get('/api/meta')
export const fetchRankings = (p) => get('/api/rankings', p)
export const fetchStock = (symbol) => get(`/api/stock/${encodeURIComponent(symbol)}`)
export const searchStocks = (q) => get('/api/search', { q })
