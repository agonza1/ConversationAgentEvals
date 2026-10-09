"""Isolate durable judge-request admission identities between independent test cases."""
import pytest


@pytest.fixture(autouse=True)
def isolated_judge_requests():
    from app.db.database import SessionLocal
    from app.models.entities import AssertJudgeRequestRecord
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
