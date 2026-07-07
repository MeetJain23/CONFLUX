# CONFLUX

**Confluence Of Numerous Factors Linking Underlying eXposures**

🔗 **Live dashboard:** [conflux.streamlit.app](https://conflux.streamlit.app)

A multi-vector confluence engine for the Indian equity market. Built on the framework
of 15 fundamental vectors that drive stock returns which are input/output material costs,
government policy, promoter quality, supply/demand-side dynamics, macros, re-rating
catalysts, global parallels, and structural cycles.

The system ingests data daily, scores each stock in its universe on each vector
independently, then aggregates into a composite *confluence score*. Top of the daily
report = stocks where 5+ vectors flash positive (or negative) simultaneously.
Confluence is the signal. Single vectors are not.

This is an **idea-generation engine**, not an auto-trader. Outputs go to a human (you).

## Architecture (four layers)

1. **Stock Metadata Graph** - hand-curated facts per stock (sectors, input commodities,
   customers, suppliers, global parents, promoter groups, peers). This is the moat.
2. **Data Ingestion Pipelines** - scheduled jobs that fetch prices, commodities, FX,
   rates, corporate announcements, policy news, insider trades, supply-disruption news.
3. **Vector Scorers** - one module per vector. Each outputs -1.0 to +1.0 per stock per day.
4. **Confluence Engine + Dashboard** - aggregates active vectors, ranks setups, Streamlit UI.

## Status

**Phase 1 (shipped Jun 9):** Foundation + V4 (input material cost) + V13 (geopolitics & macros).

**Phase 2 (V12 + V2 shipped):** V12 re-rating catalysts: (shipped Jun 15) and V2 government
policy (shipped Jun 22). V2 uses a hybrid stock-targeting design (see ADR-003):
explicit per-policy mappings as the moat, inferred fallback via existing
metadata for common patterns, unmapped events ingest but produce no signal.

**Phase 3 (V11 shipped Jun 27):** V11 global parallels:  for stocks with an
international parent company, scores the gap between the Indian subsidiary's
actual return and the β-implied expected return from parent moves. Catch-up
bullish when sub lags parent; fade bearish when sub leads. Currently scores
16 of 86 stocks (those with public parent listings in the universe).

**Phase 3 (V1 shipped Jun 28):** V1 promoters: insider trades from SEBI PIT
Regulation 7(2) disclosures. Tanh-squashed scoring with category × mode ×
signed magnitude × time decay × cluster bonus. Sparse by design: only fires
when promoter or KMP activity is disclosed within the lookback window.
Reads the underlying signal without laundering it through news sentiment.

**Phase 3 (V6 shipped Jul 4):** V6 supply disruption: discrete supply-side
events across 12 subtype categories (monsoon, Hormuz corridor, China steel /
API / critical minerals, semiconductor shortage, OPEC cuts, India natural gas,
global tariff shocks, force majeure). Mode-A-only architecture: only stocks
explicitly mapped in the moat file receive signal — same sparse-vector pattern
as V1 and V11. Per-subtype contribution cap prevents multi-day news cycle
saturation. Currently 6 of 12 subtypes actively firing given the news window;
remainder await either their event class showing up in the news cycle (OPEC
cuts, pharma API disruption) or universe expansion (natural gas via GAIL and
fertilizer names).

**Deployment (shipped Jun 25):** Public dashboard at [conflux.streamlit.app](https://conflux.streamlit.app).
Deployed via Streamlit Cloud with private Cloudflare R2 backing the SQLite DB
— preserves the hand-curated metadata moat (stock universe, input commodities,
policy mappings, supply-event mappings, promoter mappings) while making the
computed scores publicly inspectable.

**7 of 15 vectors live.** Universe: 86 stocks across 17 sectors, expanding toward Nifty 100.

## Quick start

```bash
# 1. Clone and set up environment
git clone https://github.com/MeetJain23/CONFLUX.git
cd CONFLUX
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 2. Configure your FRED API key (see Environment variables below)

# 3. Initialize the database and load metadata
python -m scripts.load_metadata

# 4. Run the daily pipeline
python -m scripts.run_daily

# 5. Launch the dashboard
streamlit run app/dashboard.py
```

Note: the `metadata/` CSVs that drive the stock universe and per-vector
mappings are gitignored as the project's moat. A fresh clone runs with
empty metadata; you'll need to populate `metadata/stocks.csv`,
`metadata/commodities.csv`, `metadata/stock_input_commodities.csv`,
`metadata/v2_policy_mappings.csv`, and `metadata/v6_supply_mappings.csv`
before the daily pipeline produces signal.

## Environment variables

Some macro data is sourced from FRED (Federal Reserve Bank of St. Louis).
Free API key at https://fred.stlouisfed.org/docs/api/api_key.html

Add to a `.env` file in the project root:
FRED_API_KEY=your_key_here

The `.env` file is gitignored.

## Roadmap

| Phase | Vectors                       | Status              |
|-------|-------------------------------|---------------------|
| 1     | V4, V13                       | shipped Jun 9       |
| 2     | V12, V2                       | both shipped Jun 22 |
| 3     | V11                           | shipped Jun 27      |
| 3     | V1                            | shipped Jun 28      |
| 3     | V6                            | shipped Jul 4       |
| 3     | V7                            | next                |
| 4     | V8, V10, V14, V15 (LLM-based) | planned             |

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