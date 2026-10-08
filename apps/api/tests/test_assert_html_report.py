"""Saved-result export tests use synthetic evidence; no provider calls or credits."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import assert_judge
from app.services.benchmark_service import get_scenario_contract
from app.services.execution_run_store import deterministic_evaluation_snapshot
from app.services.upstream_assert_judge import assert_judge_input_fingerprint

FIXTURE = Path(__file__).parent / 'fixtures/assert-html-report-run.json'
client = TestClient(app)


@pytest.fixture
def saved(monkeypatch):
    run = json.loads(FIXTURE.read_text())
    conv = run['conversations'][0]
    review = conv['judge_reviews'][0]
    monkeypatch.setattr(assert_judge.execution_run_store, 'get_execution_run', lambda run_id: deepcopy(run))
    def forbidden(**kwargs):
        pytest.fail('Export must not invoke judging, charge credits, or record judge requests.')
    monkeypatch.setattr(assert_judge, 'run_upstream_assert_judge', forbidden)
    monkeypatch.setattr(assert_judge, 'record_judge_request', forbidden)
    return run, conv, review


def download(saved, user='demo-user', review_id=None):
    run, conv, review = saved
    return client.get(f"/api/assert/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}"
                      f"/reviews/{review_id or review['review_id']}/report.html", params={'user_id': user})


def resnapshot(saved):
    run, conv, review = saved
    review['deterministic_snapshot'] = deterministic_evaluation_snapshot(conv)
    review['judge_result']['provenance']['input_fingerprint'] = assert_judge_input_fingerprint(
        run=run, conversation=conv, scenario_contract=get_scenario_contract(run['suite_id'],conv['scenario_id']),
        model=review['model'], judge_n=1)


def test_valid_download_preserves_saved_values_without_mutation(saved):
    original = deepcopy(saved)
    response = download(saved)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['content-disposition'] == 'attachment; filename="assert-exec-assert-html-fixture-conversation-refund-fixture-judge-review-fixture.html"'
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    text = response.text
    for value in ['CAE deterministic verdict:', 'needs_review', '45', '0.3.0',
                  'fixture/recorded-model', 'completion_quality', 'Review opened',
                  'case-fixture', 'refund_issued', 'unresolved citation', 'Audio is not embedded',
                  'Synthetic fixture', 'Currently applied adjudication:', 'Unavailable']:
        assert value in text
    for value in ['/Users/private', 'scores.json', '<script', 'src=', 'href="http', 'url(', 'data:audio']:
        assert value not in text
    assert saved == original


def test_wrong_owner_is_non_disclosing_before_evidence(saved, monkeypatch):
    monkeypatch.setattr(assert_judge, 'get_scenario_contract', lambda *args: pytest.fail('Evidence read before owner check'))
    response = download(saved, user='someone-else')
    assert response.status_code == 404
    assert response.json()['detail'] == 'Execution run not found.'


@pytest.mark.parametrize('visible', [False, True])
def test_project_visibility_and_exact_identity(saved, monkeypatch, visible):
    run, _, _ = saved
    run.update(project_id='shared-key', product_project_id='exact-project')
    calls = []
    def project(**kwargs):
        calls.append(kwargs)
        return object() if visible else None
    monkeypatch.setattr(assert_judge, 'execution_project_accessible', project)
    response = download(saved)
    assert response.status_code == (200 if visible else 404)
    assert calls[0]['user_id'] == 'demo-user'
    assert calls[0]['project_id'] == 'shared-key'
    assert calls[0]['product_project_id'] == 'exact-project'


@pytest.mark.parametrize('field,value', [('status','running'),('status','queued'),('status','unknown')])
@pytest.mark.parametrize('entity', [0, 1])
def test_nonterminal_unavailable(saved, field, value, entity):
    saved[entity][field] = value
    assert download(saved).status_code == 409


def test_missing_review_and_wrong_conversation(saved):
    assert download(saved, review_id='missing').status_code == 409
    run, conv, review = saved
    response = client.get(f"/api/assert/runs/{run['execution_run_id']}/conversations/wrong/reviews/{review['review_id']}/report.html?user_id=demo-user")
    assert response.status_code == 404


@pytest.mark.parametrize('collection', ['conversations', 'judge_reviews'])
@pytest.mark.parametrize('bad', [None, 'invalid', 42, []])
def test_invalid_entries_do_not_hide_valid_saved_evidence(saved, collection, bad):
    run, conv, review = saved
    parent = run if collection == 'conversations' else conv
    parent[collection].insert(0, bad)
    original = deepcopy(saved)
    assert download(saved).status_code == 200
    response = client.get(f"/api/assert/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}"
                          f"/reviews/{review['review_id']}/status", params={'user_id': 'demo-user'})
    assert response.status_code == 200 and response.json()['status'] == 'current'
    assert saved == original


@pytest.mark.parametrize('collection,expected', [('conversations', 404), ('judge_reviews', 409)])
@pytest.mark.parametrize('bad', [None, 'invalid', 42, {}, [None, 'invalid', 42]])
def test_malformed_collections_return_unavailable_without_server_error(saved, collection, expected, bad):
    run, conv, review = saved
    parent = run if collection == 'conversations' else conv
    parent[collection] = bad
    original = deepcopy(saved)
    assert download(saved).status_code == expected
    response = client.get(f"/api/assert/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}"
                          f"/reviews/{review['review_id']}/status", params={'user_id': 'demo-user'})
    assert response.status_code == 404
    assert saved == original


@pytest.mark.parametrize('change', ['turns','action_trace','final_state','verdict','run_target','scenario_contract'])
def test_stale_input_rejected(saved, monkeypatch, change):
    run, conv, _ = saved
    if change == 'run_target':
        run['agent_name'] = 'Different target'
    elif change == 'scenario_contract':
        monkeypatch.setattr(assert_judge, 'get_scenario_contract', lambda *args: {'goal':'Different behavior'})
    elif change == 'verdict':
        conv['metrics_summary']['verdict'] = 'pass'
    else:
        conv[change] = [] if change in {'turns','action_trace'} else {'complete':True}
    response = download(saved)
    assert response.status_code == 409
    assert 'stale' in response.json()['detail']


@pytest.mark.parametrize('change', ['engine','status','dimensions','nodes','fingerprint','snapshot','justifications'])
def test_malformed_or_missing_result_rejected(saved, change):
    _, _, review = saved
    p = review['judge_result']['provenance']
    if change == 'engine': p['engine'] = 'other'
    elif change == 'status': p['judge_status'] = 'error'
    elif change == 'dimensions': p['dimensions'] = {'x':{'bad':'shape'}}
    elif change == 'nodes': p['node_judgments'] = ['bad']
    elif change == 'fingerprint': p['input_fingerprint'] = None
    elif change == 'snapshot': review.pop('deterministic_snapshot')
    elif change == 'justifications': p['dimension_justifications'] = []
    assert download(saved).status_code == 409


@pytest.mark.parametrize('field', ['output_sha256', 'score_sha256'])
@pytest.mark.parametrize('bad', ['invalid', 'a' * 63, 'a' * 65, 'A' * 64, 42, True, {}])
def test_saved_digest_must_be_sha256_before_rendering(saved, field, bad):
    review = saved[2]
    source = review if field == 'output_sha256' else review['judge_result']['provenance']
    source[field] = bad
    response = download(saved)
    assert response.status_code == 409
    assert field in response.json()['detail']


@pytest.mark.parametrize('field', ['output_sha256', 'score_sha256'])
@pytest.mark.parametrize('missing', [False, True])
def test_optional_digest_is_unavailable_not_fabricated(saved, field, missing):
    review = saved[2]
    source = review if field == 'output_sha256' else review['judge_result']['provenance']
    if missing:
        source.pop(field)
    else:
        source[field] = None
    response = download(saved)
    assert response.status_code == 200
    label = 'Output SHA-256' if field == 'output_sha256' else 'Score SHA-256'
    assert f'<dt>{label}</dt><dd>Unavailable</dd>' in response.text


@pytest.mark.parametrize('bad', [None, 'Authorization: Basic SYNTHETIC-CITATION-SECRET',
                                {'Authorization': 'Basic SYNTHETIC-CITATION-SECRET'},
                                42, True, [None], [42], [{'text': 'not a persisted string'}]])
def test_malformed_citation_collection_cannot_render_or_split_secrets(saved, bad):
    saved[2]['evidence_citations'] = bad
    response = download(saved)
    assert response.status_code == 409
    assert 'citation' in response.json()['detail']
    assert 'SYNTHETIC-CITATION-SECRET' not in response.text


def test_valid_citations_keep_whole_value_context_for_redaction(saved):
    saved[2]['evidence_citations'] = ['Authorization: Basic SYNTHETIC-CITATION-SECRET', 'case-fixture']
    response = download(saved)
    assert response.status_code == 200
    assert 'SYNTHETIC-CITATION-SECRET' not in response.text
    assert '[credential omitted]' in response.text and 'case-fixture' in response.text


def test_missing_citations_are_unavailable_not_inferred(saved):
    saved[2].pop('evidence_citations')
    response = download(saved)
    assert response.status_code == 200
    assert '<ul><li>Unavailable</li></ul>' in response.text


def test_selected_historical_review_and_applied_adjudication_are_distinct(saved):
    _, conv, review = saved
    review['status'] = 'superseded'
    newer = deepcopy(review)
    newer['review_id'] = 'newer-review'
    newer['status'] = 'applied'
    newer['judge_result']['rationale'] = 'NEWER RATIONALE'
    conv['judge_reviews'].append(newer)
    conv['evaluation_adjudication'] = {'review_id':'newer-review', 'judge_result':{'proposed_evaluation':{'verdict':'pass'}}}
    response = download(saved)
    assert response.status_code == 200
    assert 'NEWER RATIONALE' not in response.text
    assert 'newer-review' in response.text
    assert '<span class="pill">needs_review</span>' in response.text


def test_xss_and_internal_evidence_fields_are_removed(saved):
    run, conv, review = saved
    evil = '<script>alert("xss")</script><img src="https://evil.example" onerror="alert(1)">'
    conv['turns'][0]['text'] = evil
    conv['action_trace'][0].update(arguments={'html':evil,'api_key':'SECRET-CREDENTIAL','authorization':'Bearer DO-NOT-EXPORT'},
                                  result={'html':evil,'path':'/Users/private/result.json'})
    review['judge_result']['rationale'] = evil
    p = review['judge_result']['provenance']
    p['dimensions'][evil] = False
    p['dimension_justifications'][evil] = evil
    p['node_judgments'][0]['justification'] = evil
    review['evidence_citations'] = [evil]
    resnapshot(saved)
    response = download(saved)
    assert response.status_code == 200
    assert '<script' not in response.text and '<img' not in response.text
    assert '&lt;script&gt;' in response.text
    assert 'SECRET-CREDENTIAL' not in response.text and 'DO-NOT-EXPORT' not in response.text
    assert '/Users/private/' not in response.text


def test_filename_is_bounded_and_header_safe(saved):
    run, conv, review = saved
    # Route IDs are not arbitrary header values; renderer also bounds/sanitizes each component.
    from app.services.assert_html_report import report_filename
    filename = report_filename('run\r\nInjected: yes', '../conversation', 'x'*1000)
    assert '\r' not in filename and '\n' not in filename and '/' not in filename
    assert len(filename) < 200


@pytest.mark.parametrize('value,secret', [
    ('tool debug password: "TOP_SECRET_DEMO"', 'TOP_SECRET_DEMO'),
    ('tool debug "password": "TOP_SECRET_DEMO"', 'TOP_SECRET_DEMO'),
    ("tool debug password: 'TOP SECRET DEMO'", 'TOP SECRET DEMO'),
    ('https://demo-user:TOP_SECRET_DEMO@api.example.invalid/private', 'TOP_SECRET_DEMO'),
    ('Inference saved to /workspace/artifacts/assert/score.json', '/workspace/artifacts'),
    ('Inference saved to C:/Projects/private/score.json', 'C:/Projects/private'),
    ({'/Users/alberto/Codex/private-secret.json':'loaded'}, '/Users/alberto'),
])
def test_export_scrubs_quoted_credentials_credential_urls_and_internal_keys(value, secret):
    from app.services.assert_html_report import _json
    assert secret not in _json(value)


def test_missing_json_evidence_is_explicitly_unavailable():
    from app.services.assert_html_report import _json
    assert _json(None) == 'Unavailable'


def test_real_project_membership_revocation_and_ambiguous_key_are_non_disclosing(saved):
    """Exercise the actual database visibility helper, including a colliding personal key."""
    import uuid
    from app.db.database import SessionLocal
    from app.models.entities import ProductProject, ProductWorkspace, ProductWorkspaceMember
    key = f'export-access-{uuid.uuid4().hex[:8]}'
    run, _, _ = saved
    with SessionLocal() as db:
        workspace = ProductWorkspace(owner_user_id='export-workspace-owner', workspace_key=key)
        db.add(workspace)
        db.flush()
        member = ProductWorkspaceMember(workspace_id=workspace.id, user_id='demo-user', role='viewer')
        shared = ProductProject(user_id='export-workspace-owner', workspace_id=workspace.id, project_key=key)
        personal = ProductProject(user_id='demo-user', project_key=key)
        db.add_all([member, shared, personal])
        db.commit()
        try:
            run['project_id'] = key
            # Both projects visible: an old run without exact identity must not guess.
            assert download(saved).status_code == 404
            run['product_project_id'] = shared.id
            assert download(saved).status_code == 200
            db.delete(member)
            db.commit()
            response = download(saved)
            assert response.status_code == 404
            assert response.json()['detail'] == 'Execution run not found.'
        finally:
            db.delete(shared)
            db.delete(personal)
            db.delete(workspace)
            db.commit()


@pytest.mark.parametrize('key', [
    'client_secret', 'clientSecret', 'CLIENT-SECRET', 'private_key', 'privateKey',
    'aws_secret_access_key', 'AWS.Secret.Access.Key', 'secret_access_key',
    'db_password', 'password_hash', 'signing_key', 'service_api_key',
    'database_secret_value', 'serviceTokenValue',
    'auth', 'http_auth', 'httpAuth', 'HTTPAuth', 'basic_auth',
    'authentication', 'authentication_headers', 'HTTPAuthorization',
    'cookies', 'http_cookies', 'httpCookies', 'HTTPCookies', 'cookie_jar', 'cookieJar',
    'cookiejar', 'set_cookies', 'session_tokens', 'session_secrets',
    'sessionid', 'session_id', 'SessionID', 'JSESSIONID', 'PHPSESSID', 'ASP.NET_SessionId',
    'sid', 'session', 'connect.sid', 'session_key', 'jwt', 'csrf', 'xsrf',
])
def test_nested_credential_key_conventions_are_omitted_from_actual_report(saved, key):
    _, conv, review = saved
    credential = f'SYNTHETIC-NEVER-EXPORT-{key}'
    evidence = {
        'business_receipt': 'case-fixture',
        'connection': {key: credential},
        'attributes': [{'key': key, 'value': credential}, {'key': 'case_id', 'value': 'case-fixture'}],
        'attribute': {'key': key, 'value': credential},
        'serialized': json.dumps({'nested': {key: credential}, 'case_id': 'case-fixture'}),
    }
    conv['action_trace'][0]['result'] = evidence
    conv['final_state']['nested_result'] = evidence
    review['judge_result']['provenance']['node_judgments'][0]['evidence'] = evidence
    resnapshot(saved)
    response = download(saved)
    assert response.status_code == 200
    assert credential not in response.text
    assert 'business_receipt' in response.text and 'case-fixture' in response.text


@pytest.mark.parametrize('key', ['client_secret', 'private_key', 'aws_secret_access_key',
                                 'privateKey', 'CLIENT-SECRET', 'aws.secret.access.key'])
@pytest.mark.parametrize('quoted', [False, True])
def test_credential_text_assignments_are_omitted_from_actual_report(saved, key, quoted):
    _, conv, review = saved
    secret = 'SYNTHETIC-ASSIGNMENT-NEVER-EXPORT'
    text = f'tool debug {key}="{secret}"' if quoted else f'tool debug {key}={secret}'
    conv['turns'][0]['text'] = text
    conv['action_trace'][0]['result'] = text
    review['judge_result']['rationale'] = text
    resnapshot(saved)
    response = download(saved)
    assert response.status_code == 200
    assert secret not in response.text
    assert '[credential omitted]' in response.text


@pytest.mark.parametrize('header,secrets', [
    ('Authorization: Basic dXNlcjpwYXNz', ['dXNlcjpwYXNz']),
    ('authorization = Negotiate SYNTHETIC-NEGOTIATE', ['SYNTHETIC-NEGOTIATE']),
    ('Proxy-Authorization: NTLM SYNTHETIC-NTLM', ['SYNTHETIC-NTLM']),
    ('proxyAuthorization: Basic SYNTHETIC-PROXY', ['SYNTHETIC-PROXY']),
    ('Proxy.Authorization: Basic SYNTHETIC-DOTTED', ['SYNTHETIC-DOTTED']),
    ('auth: Basic SYNTHETIC-AUTH', ['SYNTHETIC-AUTH']),
    ('http_auth: Digest username="SYNTHETIC-HTTP-AUTH", response="SYNTHETIC-HTTP-DIGEST"',
     ['SYNTHETIC-HTTP-AUTH', 'SYNTHETIC-HTTP-DIGEST']),
    ('HTTPAuth: Basic SYNTHETIC-ACRONYM', ['SYNTHETIC-ACRONYM']),
    ('HTTPAuthorization: Basic SYNTHETIC-HTTP-HEADER', ['SYNTHETIC-HTTP-HEADER']),
    ('Cookie: session=SYNTHETIC-SESSION; refresh=SYNTHETIC-REFRESH', ['SYNTHETIC-SESSION', 'SYNTHETIC-REFRESH']),
    ('Set-Cookie: session=SYNTHETIC-SET; Domain=internal.example; HttpOnly', ['SYNTHETIC-SET']),
    ('cookies: sessionid=SYNTHETIC-PLURAL; refresh=SYNTHETIC-PLURAL-REFRESH',
     ['SYNTHETIC-PLURAL', 'SYNTHETIC-PLURAL-REFRESH']),
    ('http_cookies={"sessionid":"SYNTHETIC-COOKIE-MAP","refresh":"SYNTHETIC-MAP-REFRESH"}',
     ['SYNTHETIC-COOKIE-MAP', 'SYNTHETIC-MAP-REFRESH']),
    ('cookieJar: sessionid=SYNTHETIC-JAR; domain=internal.example', ['SYNTHETIC-JAR']),
    ('Authorization: Digest username="SYNTHETIC-USER", nonce="SYNTHETIC-NONCE", response="SYNTHETIC-RESPONSE"',
     ['SYNTHETIC-USER', 'SYNTHETIC-NONCE', 'SYNTHETIC-RESPONSE']),
    ('"Authorization": "Basic SYNTHETIC-QUOTED"', ['SYNTHETIC-QUOTED']),
    (r'Debug "Authorization": "Digest username=\"SYNTHETIC-ESCAPED\", response=\"SYNTHETIC-DIGEST\""',
     ['SYNTHETIC-ESCAPED', 'SYNTHETIC-DIGEST']),
    ('Authorization: Digest uri="/example/<SYNTHETIC-URI>", response="SYNTHETIC-AFTER-URI"',
     ['SYNTHETIC-URI', 'SYNTHETIC-AFTER-URI']),
    ("Authorization='Basic SYNTHETIC-SINGLE-QUOTED'", ['SYNTHETIC-SINGLE-QUOTED']),
])
def test_entire_credential_header_is_scrubbed_from_actual_report(saved, header, secrets):
    _, conv, review = saved
    text = f'Debug {header}\nBusiness receipt: case-fixture'
    conv['turns'][0]['text'] = text
    conv['action_trace'][0]['result'] = text
    conv['final_state']['debug'] = text
    review['judge_result']['rationale'] = text
    review['judge_result']['provenance']['node_judgments'][0]['evidence'] = text
    resnapshot(saved)
    response = download(saved)
    assert response.status_code == 200
    assert all(secret not in response.text for secret in secrets)
    assert 'Business receipt: case-fixture' in response.text
    assert '[credential omitted]' in response.text


@pytest.mark.parametrize('key', ['output_path', 'outputPath', 'score.path', 'OUTPUT-PATHS'])
@pytest.mark.parametrize('path', ['artifacts/run/score.json', './artifacts/run/score.json',
                                 '../storage/run/score.json', r'artifacts\run\score.json',
                                 '/storage/run/score.json'])
def test_relative_internal_paths_are_omitted_across_evidence_forms(saved, key, path):
    _, conv, review = saved
    evidence = {
        'business_receipt': 'case-fixture', key: path,
        'serialized': json.dumps({key: path, 'case_id': 'case-fixture'}),
        'attributes': [{'key': key, 'value': path}, {'key': 'case_id', 'value': 'case-fixture'}],
        'attribute': {'key': key, 'value': path},
        'debug': f'File saved to {path}',
    }
    conv['action_trace'][0]['result'] = evidence
    conv['final_state']['evidence'] = evidence
    conv['turns'][0]['text'] = f'File saved to {path}'
    review['judge_result']['rationale'] = f'Debug {key}="{path}"'
    review['judge_result']['provenance']['node_judgments'][0]['evidence'] = evidence
    resnapshot(saved)
    response = download(saved)
    assert response.status_code == 200
    assert 'score.json' not in response.text
    assert 'business_receipt' in response.text and 'case-fixture' in response.text
    assert '[internal path omitted]' in response.text


def test_path_fields_are_scrubbed_even_without_a_known_internal_root():
    from app.services.assert_html_report import _clean
    assert _clean({'outputPath': 'reports/private score.json', 'case_id': 'case-fixture'}) == {'case_id': 'case-fixture'}
    assert 'private score.json' not in _clean('output_path="reports/private score.json"')


@pytest.mark.parametrize('key', ['artifact_dir', 'output_dir', 'save_dir', 'workdir', 'cwd', 'pwd',
                                'working_directory', 'workspace_dirs', 'outputFolder', 'score_filename',
                                'output_root', 'repo_root', 'artifact_dir_ref'])
def test_directory_fields_are_omitted_without_a_known_internal_prefix(saved, key):
    _, conv, review = saved
    path = 'runs/private/SYNTHETIC-PRIVATE-REPORT'
    evidence = {key: path, 'business_receipt': 'case-fixture',
                'serialized': json.dumps({key: path, 'case_id': 'case-fixture'}),
                'attributes': [{'key': key, 'value': path}, {'key': 'case_id', 'value': 'case-fixture'}]}
    conv['action_trace'][0]['result'] = evidence
    conv['final_state']['evidence'] = evidence
    conv['turns'][0]['text'] = f'Debug {key}="{path}"'
    review['judge_result']['provenance']['node_judgments'][0]['evidence'] = evidence
    resnapshot(saved)
    original = deepcopy(saved)
    response = download(saved)
    assert response.status_code == 200
    assert 'SYNTHETIC-PRIVATE-REPORT' not in response.text
    assert 'case-fixture' in response.text
    assert saved == original


@pytest.mark.parametrize('key', ['cookies', 'http_cookies', 'HTTPCookies', 'cookieJar', 'cookiejar'])
def test_cookie_container_context_is_preserved_before_recursion(saved, key):
    _, conv, review = saved
    credentials = {key: {'sessionid': 'SYNTHETIC-SESSION-ID', 'other': 'SYNTHETIC-COOKIE-VALUE'},
                   'business_receipt': 'case-fixture'}
    conv['action_trace'][0]['result'] = credentials
    conv['final_state']['cookies_debug'] = credentials
    conv['turns'][0]['text'] = json.dumps(credentials)
    review['judge_result']['provenance']['node_judgments'][0]['evidence'] = {
        'attributes': [{'key': key, 'value': credentials[key]}, {'key': 'case_id', 'value': 'case-fixture'}],
        'serialized': json.dumps(credentials),
    }
    resnapshot(saved)
    original = deepcopy(saved)
    response = download(saved)
    assert response.status_code == 200
    assert 'SYNTHETIC-SESSION-ID' not in response.text and 'SYNTHETIC-COOKIE-VALUE' not in response.text
    assert 'case-fixture' in response.text
    assert saved == original
