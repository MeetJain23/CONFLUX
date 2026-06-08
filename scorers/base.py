"""
Base class for all vector scorers.

Every Vector scorer in CONFLUX inherits from VectorScorer and implements
score_one(stock, date) → (score, confidence, rationale, components).

Why this matters:
- The confluence engine treats every vector uniformly. New vectors plug in.
- All scores normalized to [-1.0, +1.0]; confidence in [0.0, 1.0].
- Rationale strings make the dashboard explainable instead of a black box.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Any
import json
import logging

from data.schema import VectorScore, get_session

logger = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    score: float                              # [-1.0, +1.0]
    confidence: float = 1.0                   # [0.0, 1.0]
    rationale: str = ""
    components: dict = field(default_factory=dict)

    def __post_init__(self):
        # defensive clipping — never let a bug leak garbage into the DB
        self.score = max(-1.0, min(1.0, float(self.score)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))


class VectorScorer(ABC):
    """All vector scorers inherit from this."""

    vector_id: int = 0          # 1..15 — must be set by subclass
    vector_name: str = ""       # human-readable

    def __init__(self, session=None):
        self.session = session or get_session()

    @abstractmethod
    def score_one(self, stock, asof: date_type) -> ScoreResult | None:
        """
        Score a single stock for a single date.
        Return None if insufficient data (do NOT fabricate a score).
        """
        ...

    def score_universe(self, stocks, asof: date_type) -> dict[int, ScoreResult]:
        """Score every stock in the given list for one date."""
        results = {}
        for stock in stocks:
            try:
                res = self.score_one(stock, asof)
                if res is not None:
                    results[stock.id] = res
            except Exception as e:
                logger.exception(f"V{self.vector_id} failed on {stock.symbol_nse}: {e}")
        return results

    def write_scores(self, results: dict, asof: date_type):
        """Persist scores. Uses INSERT OR REPLACE semantics by upserting."""
        for stock_id, res in results.items():
            existing = (
                self.session.query(VectorScore)
                .filter_by(stock_id=stock_id, vector_id=self.vector_id, date=asof)
                .first()
            )
            if existing:
                existing.score = res.score
                existing.confidence = res.confidence
                existing.rationale = res.rationale
                existing.components_json = json.dumps(res.components, default=str)
            else:
                self.session.add(
                    VectorScore(
                        stock_id=stock_id,
                        vector_id=self.vector_id,
                        date=asof,
                        score=res.score,
                        confidence=res.confidence,
                        rationale=res.rationale,
                        components_json=json.dumps(res.components, default=str),
                    )
                )
        self.session.commit()
        logger.info(f"V{self.vector_id} {self.vector_name}: wrote {len(results)} scores for {asof}")
