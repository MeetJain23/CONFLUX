"""Smoke test: schema initializes without errors."""

import tempfile
import os

from data.schema import init_db, get_session, Stock


def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        engine = init_db(db_path)
        assert os.path.exists(db_path)
        assert engine is not None
        engine.dispose()


def test_can_insert_and_query_stock():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        init_db(db_path)
        session = get_session(db_path)
        session.add(Stock(symbol_nse="TEST", symbol_yf="TEST.NS",
                          name="Test Stock", sector="IT"))
        session.commit()
        assert session.query(Stock).filter_by(symbol_nse="TEST").first().name == "Test Stock"
        session.close()
        session.bind.dispose()