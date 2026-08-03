# ADR-004: Breadth-weighted composite score

**Status:** Accepted
**Date:** Aug 2026

## Context
The composite was a confidence-weighted MEAN of active vector scores. A mean
normalizes away breadth: a stock with one vector at 1.0 scored the same 1.0 as
the ceiling, and outranked a stock with three vectors converging at 0.857. On
the live 1,482-stock universe this put lone-vector international stocks
(Hitachi, Apple, Shell — each n_active=1) at the top of the rankings, above
genuine multi-vector consensus names (L&T n=3, Grasim/UltraTech n=2). That
inverts the project thesis: "single vectors are noise, confluence is signal."

## Decision
Multiply the confidence-weighted mean by a saturating breadth factor:

    composite = raw_mean * (n_active / (n_active + k)),  k = 2

- k chosen empirically against the real universe. k=1 under-punishes lone
  vectors (a 1-vector 1.0 stays mid-pack); k=3 over-punishes strong 2-vector
  stocks. k=2 sinks single-vector signals (factor 0.33), lets a 3-vector
  consensus lead, and prevents a pile of weak vectors from runaway-winning
  (diminishing returns: 1→.33, 2→.50, 3→.60, 5→.71, 7→.78).
- Interpretation: a signal must overcome the weight of ~2 absent vectors.
- raw_mean is retained in code (transparency); only the stored composite is
  shrunk.
- Direction thresholds (±0.20) intentionally apply to the SHRUNK composite, so
  direction is breadth-aware too: a lone weak vector reads "neutral," not
  "bullish." Direction and ranking tell the same story.

## Consequences
- Composites are re-computed for all dates going forward. Historical dates
  computed under the old mean are inconsistent until backfilled; acceptable
  since the engine is pre-launch.
- Fewer stocks carry a bullish/bearish label (thin signals default neutral).
- One new tunable (k). Revisit as evidence accumulates on which vectors carry
  alpha (pairs naturally with the existing VECTOR_WEIGHTS knob).
