from datetime import datetime, timedelta
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.entities import BenchmarkSuiteRunRecord, ProductProject, ProductSavedRun
from app.services import execution_run_store

client = TestClient(app)


def test_warm_execution_history_does_not_walk_saved_run_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(execution_run_store, '_RUNS', {})

    def save(run_id, score):
        folder = tmp_path / run_id
        folder.mkdir()
        (folder / 'run.json').write_text(json.dumps({
            'execution_run_id': run_id, 'user_id': 'history-user', 'project_id': 'history-project',
            'updated_at': run_id, 'status': 'completed',
            'conversations': [{'score': score, 'transcript': 'Full retained evidence'}],
        }), encoding='utf-8')

    save('run-01', 70)
    save('run-02', 90)
    params = dict(user_id='history-user', project_id='history-project', summary=True, limit=1)
    assert execution_run_store.list_execution_runs(**params)[0]['execution_run_id'] == 'run-02'
    scandir = os.scandir

    def root_only_scan(path):
        assert Path(path).parent != tmp_path, 'Warm history must not enter each retained run directory'
        return scandir(path)

    monkeypatch.setattr(os, 'scandir', root_only_scan)
    assert execution_run_store.list_execution_runs(**params)[0]['conversations'] == [{'score': 90}]
    # Discovery remains live: another process can add a saved snapshot.
    save('run-03', 100)
    assert execution_run_store.list_execution_runs(**params)[0]['execution_run_id'] == 'run-03'
    assert execution_run_store.get_execution_run('run-03')['conversations'][0]['transcript'] == 'Full retained evidence'


def test_execution_pages_filter_before_slice_and_keep_evidence_in_detail(monkeypatch, tmp_path):
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path)
    records = {
        f'page-{index}': {
            'execution_run_id': f'page-{index}', 'user_id': 'paging-user',
            'project_id': 'paging-project', 'status': 'running' if index % 2 else 'completed',
            'updated_at': f'2026-01-{index + 1:02d}', 'mode': 'text',
            'conversations': [{'score': index, 'transcript': 'retained evidence', 'turns': ['large payload']}],
        } for index in range(15)
    }
    records['private'] = {**records['page-14'], 'execution_run_id': 'private', 'user_id': 'other-user'}
    monkeypatch.setattr(execution_run_store, '_RUNS', records)
    params = {'user_id': 'paging-user', 'project_id': 'paging-project', 'limit': 3, 'summary': 'true', 'status': 'active'}
    first = client.get('/api/execution/runs', params=params)
    assert first.status_code == 200
    assert [row['execution_run_id'] for row in first.json()] == ['page-13', 'page-11', 'page-9']
    second = client.get('/api/execution/runs', params={**params, 'offset': 3}).json()
    assert [row['execution_run_id'] for row in second] == ['page-7', 'page-5', 'page-3']
    assert first.json()[0]['conversations'] == [{'score': 13}]
    assert client.get('/api/execution/runs/page-13', params={'user_id': 'paging-user'}).json()['conversations'][0]['transcript'] == 'retained evidence'
    assert client.get('/api/execution/runs', params={**params, 'offset': 99}).json() == []
    assert len(client.get('/api/execution/runs', params={'user_id': 'paging-user'}).json()) == 15


def test_saved_and_suite_pages_preserve_access_filters_and_stable_ties():
    user = 'results-pagination-user'
    now = datetime(2026, 1, 1)
    with SessionLocal() as db:
        db.query(ProductSavedRun).filter(ProductSavedRun.user_id.in_([user, user + '-other'])).delete()
        db.query(ProductProject).filter(ProductProject.user_id.in_([user, user + '-other'])).delete()
        db.query(BenchmarkSuiteRunRecord).filter(BenchmarkSuiteRunRecord.user_id.in_([user, user + '-other'])).delete()
        for owner in [user, user + '-other']:
            project = ProductProject(id=owner, user_id=owner, project_key='paging-project', name='Paging')
            db.add(project)
            for index in range(13):
                db.add(ProductSavedRun(id=f'{owner}-{index:02d}', user_id=owner, project_id=owner,
                                       report_json=json.dumps({'suite_id': 'paging-suite', 'scenario_id': str(index % 2)}), created_at=now))
                db.add(BenchmarkSuiteRunRecord(id=f'{owner}-{index:02d}', user_id=owner, project_key='paging-project',
                                              suite_id='paging-suite', status='running' if index == 0 else 'queued' if index == 2 else 'completed' if index % 2 else 'failed',
                                              created_at=now, updated_at=now, retained_until=now + timedelta(days=3650)))
        db.commit()
    try:
        params = {'user_id': user, 'project_id': 'paging-project', 'limit': 10}
        for endpoint, key in [('/api/product/runs', 'id'), ('/api/benchmarks/suite-runs', 'suite_run_id')]:
            first = client.get(endpoint, params=params)
            assert first.status_code == 200
            second = client.get(endpoint, params={**params, 'offset': 10}).json()
            assert len(first.json()) == 10 and len(second) == 3
            assert {row[key] for row in first.json()}.isdisjoint(row[key] for row in second)
            assert first.json()[0][key] == user + '-12'
            assert client.get(endpoint, params={**params, 'project_id': 'other-project'}).json() == []
            assert len(client.get(endpoint, params={'user_id': user}).json()) == 13
        filtered = client.get('/api/benchmarks/suite-runs', params={**params, 'status': 'completed', 'limit': 2, 'offset': 2}).json()
        assert [row['suite_run_id'] for row in filtered] == [user + '-07', user + '-05']
        active = client.get('/api/benchmarks/suite-runs', params={**params, 'status': 'active', 'limit': 1}).json()
        assert [row['suite_run_id'] for row in active] == [user + '-02']
        saved = client.get('/api/product/runs', params={**params, 'scenario_id': '1', 'limit': 2, 'offset': 2}).json()
        assert [row['id'] for row in saved] == [user + '-07', user + '-05']
    finally:
        with SessionLocal() as db:
            db.query(ProductSavedRun).filter(ProductSavedRun.user_id.in_([user, user + '-other'])).delete()
            db.query(ProductProject).filter(ProductProject.user_id.in_([user, user + '-other'])).delete()
            db.query(BenchmarkSuiteRunRecord).filter(BenchmarkSuiteRunRecord.user_id.in_([user, user + '-other'])).delete()
            db.commit()


def test_result_endpoints_reject_unbounded_or_negative_page_parameters():
    for endpoint in ['/api/execution/runs', '/api/product/runs', '/api/benchmarks/suite-runs']:
        for invalid in [{'limit': 0}, {'limit': 101}, {'offset': -1}]:
            assert client.get(endpoint, params={'user_id': 'paging-user', **invalid}).status_code == 422
