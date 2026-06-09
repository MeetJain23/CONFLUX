# ADR-002: Vector scores in [-1.0, +1.0] with separate confidence

**Status:** Accepted
**Date:** Phase 1

## Context
Each vector scorer outputs a number describing whether a force is bullish (+) or
bearish (-) for a stock right now. We need a uniform range so the confluence
engine can aggregate across vectors.

## Decision
- `score` ∈ [-1.0, +1.0], symmetric around zero
- separate `confidence` ∈ [0.0, 1.0] indicating data quality / metadata coverage
- composite = confidence-weighted mean of scores

## Rationale
- Symmetric range avoids bias toward bullish or bearish
- tanh squashing makes extreme raw values saturate gracefully
- Separating confidence from score means a partial-data stock doesn't get
  artificially neutralized by a near-zero score — it gets *down-weighted* properly

## Alternatives considered
- [0, 1] one-sided: rejected, can't represent bearish
- Probability of outperformance: rejected, requires labeled outcomes (Phase 2+)
- Z-scores: rejected, unbounded and harder to aggregate uniformly
