"""Smoke test: ScoreResult clips out-of-range values."""

from scorers.base import ScoreResult


def test_score_clipping():
    r = ScoreResult(score=2.5, confidence=1.5)
    assert r.score == 1.0
    assert r.confidence == 1.0

    r = ScoreResult(score=-3.0, confidence=-0.2)
    assert r.score == -1.0
    assert r.confidence == 0.0


def test_default_confidence():
    r = ScoreResult(score=0.3)
    assert r.confidence == 1.0
