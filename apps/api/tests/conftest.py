"""Isolate durable judge-request admission identities between independent test cases."""
import pytest


@pytest.fixture(autouse=True)
def isolated_judge_requests():
    from app.db.database import SessionLocal, engine
    from app.models.entities import AssertJudgeRequestRecord
    # Focused test runs do not necessarily collect modules that initialize DB.
    AssertJudgeRequestRecord.__table__.create(bind=engine, checkfirst=True)
    with SessionLocal() as db:
        db.query(AssertJudgeRequestRecord).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(AssertJudgeRequestRecord).delete()
        db.commit()


@pytest.fixture
def assert_pipeline(monkeypatch, tmp_path):
    from assert_test_helpers import AssertPipeline
    return AssertPipeline(monkeypatch, tmp_path)
