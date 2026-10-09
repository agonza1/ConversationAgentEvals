"""Synthetic provenance helpers. Never invoke a provider or assert model accuracy."""
from copy import deepcopy
from app.services.evaluation_contract import freeze_contract, recorded_contract
from app.services.execution_run_store import deterministic_evaluation_snapshot
from app.services.upstream_assert_judge import judge_configuration, assert_judge_input_fingerprint, FINGERPRINT_VERSION


def freeze(conversation, contract=None):
    if contract is None:
        from app.services.benchmark_service import get_scenario_contract
        contract = get_scenario_contract(conversation.get('suite_id', ''), conversation.get('scenario_id', '')) or {'goal': 'Review this synthetic conversation.'}
    conversation.setdefault('evaluation_findings', {})['evaluation_contract_snapshot'] = freeze_contract(
        contract, suite_id=conversation.get('suite_id', ''), scenario_id=conversation.get('scenario_id', ''))
    return recorded_contract(conversation)


def refresh_review(run, conversation, review):
    if 'evaluation_contract_snapshot' not in (conversation.get('evaluation_findings') or {}):
        freeze(conversation)
    contract = recorded_contract(conversation)
    provenance = review['judge_result'].setdefault('provenance', {})
    n = provenance.get('judge_n', 1)
    provenance.update(engine='assert', judge_status='ok', assert_version='0.3.0',
                      fingerprint_version=FINGERPRINT_VERSION, judge_n=n,
                      configuration=judge_configuration(review['model'], n),
                      contract_snapshot_sha256=contract['_snapshot_sha256'])
    provenance['input_fingerprint'] = assert_judge_input_fingerprint(run=run, conversation=conversation,
                                                                  scenario_contract=contract, model=review['model'], judge_n=n)
    review['deterministic_snapshot'] = deterministic_evaluation_snapshot(conversation)


def response_for(run, conversation, *, proposed='needs_review'):
    review = {'model': 'openai/gpt-4.1-mini', 'judge_result': {
        'agrees': conversation.get('verdict') == proposed, 'rationale': 'Synthetic test judgment.',
        'next_action': 'Inspect retained evidence.',
        'proposed_evaluation': {'verdict': proposed, 'summary': 'Synthetic result', 'corrected_findings': [], 'remaining_gaps': []},
    }}
    refresh_review(run, conversation, review)
    return {'status': 'ready', 'provider': 'assert-ai', 'engine': 'assert', 'required_plan': 'starter',
            'credits': 10, 'model': review['model'], 'judge_n': 1, 'judge_output': '{}',
            'message': 'Synthetic ASSERT review.', 'evidence_citations': [], 'judge_result': review['judge_result']}


class AssertPipeline:
    """Real HTTP persistence/admission/adapter/CLI validation, fake provider output only."""
    def __init__(self, monkeypatch, tmp_path):
        import json
        import subprocess
        import yaml
        from pathlib import Path
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services import judge_budget, upstream_assert_judge, execution_run_store
        from app.services.benchmark_run_store import reset_benchmark_run_records_for_tests
        from app.services.product_service import reset_saved_runs_for_tests
        self.client = TestClient(app)
        self.calls = []
        self.flags = {}
        self.raise_error = None
        self.pause = None
        monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
        monkeypatch.setenv('ASSERT_JUDGE_MODEL', 'openai/gpt-4.1-mini')
        monkeypatch.setenv('ASSERT_JUDGE_MAX_N', '1')
        monkeypatch.setenv('ASSERT_JUDGE_MAX_CONCURRENT', '2')
        monkeypatch.setenv('LLM_JUDGE_API_KEY', 'synthetic-no-network-key')
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', '200')
        monkeypatch.setenv('LLM_JUDGE_RESERVED_DAILY_CREDITS', '0')
        monkeypatch.setattr(judge_budget, '_judge_spend_path', lambda: tmp_path/'spend.json')
        monkeypatch.setattr(execution_run_store, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path/'runs')
        monkeypatch.setattr(execution_run_store, '_RUNS', {})
        monkeypatch.setattr(upstream_assert_judge, '_ASSERT_JUDGE_ACTIVE', 0)
        reset_benchmark_run_records_for_tests()
        reset_saved_runs_for_tests()

        def fake(command, **kwargs):
            from app.integrations.assert_runtime import judge_score_contract
            self.calls.append(command)
            if self.pause:
                self.pause()
            if self.raise_error:
                raise self.raise_error
            config = yaml.safe_load(Path(command[command.index('--config')+1]).read_text())['pipeline']['judge']
            row = json.loads(Path(config['inference_set_path']).read_text())
            taxonomy = json.loads(Path(config['taxonomy_path']).read_text())
            contract = judge_score_contract(config['dimensions'])
            dimensions = {key: self.flags.get(key, (key == 'unverified_operational_outcome'
                and (row.get('dimensions') or {}).get('evidence_level') == 'black_box'))
                for key in contract['score_keys']}
            score = {'type': 'scenario', 'test_case_id': row['test_case_id'],
                     'target': row.get('target'), 'judge_model': config['model']['name'],
                     'judge_status': 'ok', 'judge_error': None, **contract,
                     'verdict': {'dimensions': dimensions,
                        'dimension_justifications': {key: 'Synthetic test output [1].' for key in dimensions},
                        'node_judgments': [{'node_name': cat['name'], 'violated': False, 'confidence': 'high',
                                           'reasoning': 'Synthetic fixture, not an accuracy measurement.'}
                                          for cat in taxonomy['behavior_categories']],
                        'narrative': 'Synthetic policy review; keep recorded evidence authoritative.'}}
            Path(config['save_dir'], 'scores.jsonl').write_text(json.dumps(score)+'\n')
            return subprocess.CompletedProcess(command, 0, '{}', '')
        monkeypatch.setattr(upstream_assert_judge.subprocess, 'run', fake)

    def evaluate(self, *, user_id='owner', project_id='project', transcript=None, **kwargs):
        payload = {'suite_id': 'call-center-voice-ai', 'scenario_id': 'refund-policy-boundary',
                   'user_id': user_id, 'project_id': project_id, **kwargs}
        if transcript is not None:
            payload['transcript'] = transcript
        elif 'vcon' not in payload:
            payload['transcript'] = 'User: Please review this charge.\nAgent: I can explain the refund process.'
        response = self.client.post('/api/benchmarks/run', json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def judge(self, source, **kwargs):
        return self.client.post(f"/api/assert/benchmarks/{source['run_id']}/judge",
                                json={'user_id': source['run_metadata']['user_id'], **kwargs})

    def spent(self):
        from app.services.judge_budget import _load_judge_spend
        return _load_judge_spend()['spent']
