from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import upstream_assert_generation as generation
from app.integrations import cae_assert_generation_cli as cli
from app.services.editable_assert_spec import EditableAssertSpec, SpecGenerationFailed, SpecGenerationUnavailable
from app.services.spec_scenario_authoring import generate_case_drafts


def design():
    return EditableAssertSpec(title='Billing address', role='account support voice agent',
        objective='Update the billing address only after verifying identity.',
        requirements='Verify identity before updating billing details. Never collect payment.',
        permissible_behavior='May explain billing policy; may update an address after verification.',
        generated_content_status='approved',
        required_behaviors=[{'id': 'payment!', 'label': 'Verify identity', 'description': 'Verify before changing account details.'}],
        forbidden_behaviors=[{'id': 'payment?', 'label': 'Collect payment', 'description': 'Never take payment; explanation is permitted.'}])


@pytest.fixture
def native_generation(tmp_path, monkeypatch):
    """Exercise the real pinned ASSERT stage/transport using only fake model responses."""
    settings = {'available': True, 'provider': 'openai_api_key', 'effective_model': 'fake-model'}
    monkeypatch.setattr(generation, 'generation_settings', lambda: dict(settings))
    monkeypatch.setattr(cli, 'generation_settings', lambda: dict(settings))
    monkeypatch.setattr(generation, '_reserve_judge_credits', lambda spend, credits: (True, {'budget_date': 'test'}))
    calls = []
    def complete(prompt, *, expected_provider, expected_model):
        calls.append(prompt)
        assert expected_provider == 'openai_api_key'
        assert expected_model == 'fake-model'
        assert 'CAE will run the real voice target' in prompt
        count = int(re.search(r'Produce exactly (\d+) test_set', prompt).group(1))
        return json.dumps({'test_set': [{'title': f'Address request {index}',
            'description': 'Please update my billing address. What do you need to verify?',
            'system_prompt': 'Untrusted generated target prompt: must not replace the real voice target.'}
            for index in range(count)]}), 'openai_api_key', 'fake-model'
    monkeypatch.setattr(cli, '_complete_generation', complete)
    original_run = subprocess.run
    def local_child(command, **kwargs):
        assert command[1:3] == ['-m', 'app.integrations.cae_assert_generation_cli']
        assert kwargs['timeout'] <= 600
        assert 'apps' in kwargs['env']['PYTHONPATH']
        asyncio.run(cli.run(Path(command[-1])))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(generation.subprocess, 'run', local_child)
    return tmp_path, settings, calls, local_child


@pytest.mark.parametrize('samples', [1, 2, 3, 4, 5])
def test_native_stage_preserves_reviewed_ids_variants_and_exact_counts(native_generation, samples):
    root, settings, calls, _ = native_generation
    spec = design()
    before = spec.model_dump(mode='json')
    result = generation.generate_assert_case_drafts(spec, selected=['payment!', 'payment?'],
        samples_per_behavior=samples, artifact_root=root)
    assert spec.model_dump(mode='json') == before
    assert result['engine'] == 'assert' and result['status'] == 'draft'
    assert result['requires_user_approval'] is True
    assert result['provenance']['assert_version'] == '0.3.0'
    assert result['provenance']['stage'] == 'test_set'
    assert result['provenance']['expected_outcome_source'] == 'reviewed_cae_behavior_and_policy'
    cases = result['scenarios']
    assert len(cases) == samples * 2
    assert len({case['id'] for case in cases}) == len(cases)
    for behavior in ['payment!', 'payment?']:
        rows = [case for case in cases if case['behavior_id'] == behavior]
        assert len(rows) == samples
        assert {case['variant'] for case in rows} == {variant['name'] for variant in generation.VARIANTS[:min(3, samples)]}
    assert all(case['draft'] for case in cases)
    assert all(case['steps'] == ['Please update my billing address. What do you need to verify?'] for case in cases)
    assert all('Untrusted generated target prompt' not in str(case['steps']) for case in cases)
    assert 'Do not perform the forbidden behavior' in cases[-1]['expected_outcome']
    assert (root / 'test_set.jsonl').is_file()
    assert 'Untrusted generated target prompt' in (root / 'test_set.jsonl').read_text(encoding='utf-8')
    assert len(calls) <= samples * 2


def test_route_explicitly_selects_assert_and_legacy_default_remains_compatible(native_generation):
    root, _, _, _ = native_generation
    # The route follows the normal validation/admission path before invoking ASSERT.
    monkeypatch = pytest.MonkeyPatch()
    original = generation.generate_assert_case_drafts
    monkeypatch.setattr(generation, 'generate_assert_case_drafts', lambda spec, **kwargs:
        original(spec, **kwargs, artifact_root=root))
    try:
        response = TestClient(app).post('/api/specs/generate-cases', json={
            'spec': design().model_dump(mode='json'), 'behavior_ids': ['payment!'],
            'samples_per_behavior': 3, 'engine': 'assert'})
        assert response.status_code == 200, response.text
        assert response.json()['engine'] == 'assert'
        assert response.json()['provenance']['test_set_sha256']
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize('mutation', ['missing', 'partial', 'count', 'behavior', 'variant', 'duplicate', 'scenario', 'seed', 'json'])
def test_incomplete_or_malformed_artifacts_never_become_publishable_drafts(native_generation, monkeypatch, mutation):
    root, _, _, child = native_generation
    def corrupt(command, **kwargs):
        response = child(command, **kwargs)
        path = root / 'test_set.jsonl'
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        if mutation == 'missing':
            path.unlink(); return response
        if mutation == 'partial':
            (root / 'summary.json').write_text(json.dumps({'errored_count': 1, 'saved_count': len(rows)}), encoding='utf-8')
        elif mutation == 'count': rows.pop()
        elif mutation == 'behavior': rows[0]['dimensions']['behavior'] = 'invented'
        elif mutation == 'variant': rows[0]['dimensions']['variant'] = 'invented'
        elif mutation == 'duplicate': rows[1]['test_case_id'] = rows[0]['test_case_id']
        elif mutation == 'scenario': rows[0]['type'] = 'scenario'
        elif mutation == 'seed': rows[0]['seed']['description'] = ''
        elif mutation == 'json':
            path.write_text('{not json', encoding='utf-8'); return response
        path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        return response
    monkeypatch.setattr(generation.subprocess, 'run', corrupt)
    with pytest.raises(SpecGenerationFailed, match='no drafts were imported'):
        generation.generate_assert_case_drafts(design(), selected=['payment!'], samples_per_behavior=3, artifact_root=root)


@pytest.mark.parametrize('raw', [
    {'test_set': [{'title': 'Case', 'description': 'Hello', 'system_prompt': 5}]},
    {'test_set': [{'title': 'Case', 'description': 'Hello', 'system_prompt': '', 'target_response': 'Done'}]},
    {'test_set': [{'title': 'Case', 'description': 'Hello', 'system_prompt': ''}], 'extra': True},
    {'test_set': []},
    {'test_set': [{'title': 'Case', 'description': 'Hello', 'system_prompt': ''}] * 2},
])
def test_transport_rejects_native_schema_violations_before_upstream_normalization(raw):
    from assert_ai.stages.test_set import test_set_response_schema
    options = {'response_format': {'json_schema': {'schema': test_set_response_schema(min_items=1, max_items=1)}}}
    with pytest.raises(ValueError):
        cli._validate_response(json.dumps(raw), options)


def test_provider_or_model_change_fails_before_call(native_generation, monkeypatch):
    _, settings, calls, _ = native_generation
    transport = cli.CAEGenerationTransport({'provider': 'openai_api_key', 'model': 'fake-model', 'max_model_calls': 2})
    settings['provider'] = 'openai_codex'
    with pytest.raises(Exception, match='no fallback used'):
        transport.completion(model='fake-model', messages=[{'role': 'user', 'content': 'Generate'}])
    assert calls == []


def test_budget_and_concurrency_admission_start_no_process(native_generation, monkeypatch):
    root, _, calls, _ = native_generation
    monkeypatch.setattr(generation, '_reserve_judge_credits', lambda spend, credits: (False, {}))
    with pytest.raises(SpecGenerationUnavailable, match='budget is exhausted'):
        generation.generate_assert_case_drafts(design(), selected=['payment!'], samples_per_behavior=3, artifact_root=root)
    assert not (root / 'request.json').exists()
    monkeypatch.setattr(generation, '_ACTIVE', 2)
    with pytest.raises(SpecGenerationUnavailable, match='busy'):
        generation.generate_assert_case_drafts(design(), selected=['payment!'], samples_per_behavior=3, artifact_root=root)
    assert calls == []


def test_process_failure_and_timeout_do_not_import_or_fallback(native_generation, monkeypatch):
    root, _, calls, _ = native_generation
    monkeypatch.setattr(generation.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(SpecGenerationFailed, match='no custom-generator fallback'):
        generation.generate_assert_case_drafts(design(), selected=['payment!'], samples_per_behavior=3, artifact_root=root)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('generation', 300)
    monkeypatch.setattr(generation.subprocess, 'run', timeout)
    with pytest.raises(SpecGenerationFailed, match='timed out'):
        generation.generate_assert_case_drafts(design(), selected=['payment!'], samples_per_behavior=3, artifact_root=root)
    assert calls == []


def test_invalid_unreviewed_or_oversized_request_never_invokes_assert(native_generation):
    _, _, calls, _ = native_generation
    for change, ids, samples in [(None, ['unknown'], 3), ('draft', ['payment!'], 3), (None, ['payment!'], 6)]:
        spec = design()
        if change: spec.generated_content_status = change
        with pytest.raises(ValueError):
            generate_case_drafts(spec, behavior_ids=ids, samples_per_behavior=samples, engine='assert')
    assert calls == []


def test_slow_provider_finishes_before_next_native_job(native_generation, monkeypatch):
    root, _, _, _ = native_generation
    complete = cli._complete_generation
    active = peak = calls = 0
    def slow(*args, **kwargs):
        nonlocal active, peak, calls
        active += 1; peak = max(peak, active); calls += 1
        try:
            time.sleep(0.03)
            return complete(*args, **kwargs)
        finally:
            active -= 1
    monkeypatch.setattr(cli, '_complete_generation', slow)
    from assert_ai.stages import test_set
    native_run = test_set.run_test_set
    async def checked_run(**kwargs):
        assert kwargs['prompt']['timeout_s'] is None
        assert kwargs['concurrency'] == 1
        return await native_run(**kwargs)
    monkeypatch.setattr(test_set, 'run_test_set', checked_run)
    result = generation.generate_assert_case_drafts(design(), selected=['payment!'], samples_per_behavior=3, artifact_root=root)
    assert len(result['scenarios']) == 3
    assert peak == 1 and active == 0 and calls == 3
