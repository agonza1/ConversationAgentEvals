from __future__ import annotations

import json

import pytest

from app.services.llm_providers import openai_codex as codex


def _completed(content=None, **response):
    return {'type': 'response.completed', 'response': {'status': 'completed', 'output': [
        {'type': 'message', 'role': 'assistant', 'content': content or []},
    ], **response}}


def _wire(events):
    return ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n'


def _stream(monkeypatch, events):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter(_wire(events).encode().splitlines(keepends=True))

    monkeypatch.setattr(codex.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    return codex._http_json_post_stream('https://example.invalid/responses', {}, headers={})


@pytest.mark.parametrize('mode', ['stream', 'buffered'])
@pytest.mark.parametrize('prefix', [
    [],
    [{'type': 'response.output_text.delta', 'delta': 'Final answer'}],
    [{'type': 'response.output_text.done', 'text': 'Final answer'}],
    [{'type': 'response.output_text.delta', 'delta': 'Final'},
     {'type': 'response.output_text.delta', 'delta': ' answer'},
     {'type': 'response.output_text.done', 'text': 'Final answer'}],
])
def test_completed_envelope_fallback_never_duplicates_answer(monkeypatch, mode, prefix):
    events = [*prefix, _completed([{'type': 'output_text', 'text': 'Final answer'}])]
    actual = ''.join(_stream(monkeypatch, events)) if mode == 'stream' else codex._parse_responses_sse(_wire(events))['output_text']
    assert actual == 'Final answer'


@pytest.mark.parametrize('mode', ['stream', 'buffered'])
def test_envelope_fallback_extracts_only_assistant_output_text(monkeypatch, mode):
    event = _completed([
        {'type': 'reasoning_text', 'text': 'Private reasoning'},
        {'type': 'output_text', 'text': 'Spoken answer'},
    ])
    event['response']['output'].extend([
        {'type': 'reasoning', 'content': [{'type': 'output_text', 'text': 'Not a message'}]},
        {'type': 'message', 'role': 'user', 'content': [{'type': 'output_text', 'text': 'User input'}]},
        {'type': 'function_call', 'content': [{'type': 'output_text', 'text': 'Tool data'}]},
    ])
    event['response']['output_text'] = 'Untrusted convenience field'
    actual = ''.join(_stream(monkeypatch, [event])) if mode == 'stream' else codex._parse_responses_sse(_wire([event]))['output_text']
    assert actual == 'Spoken answer'


@pytest.mark.parametrize('mode', ['stream', 'buffered'])
@pytest.mark.parametrize(('terminal', 'message'), [
    ({'type': 'response.failed', 'response': {'status': 'failed', 'error': {'code': 'server_error', 'message': 'Private payload'}}},
     'Codex Responses stream failed (server_error).'),
    ({'type': 'response.incomplete', 'response': {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}},
     'Codex Responses stream incomplete (max_output_tokens).'),
    ({'type': 'error', 'code': 'rate_limit_exceeded', 'message': 'Private payload'},
     'Codex Responses stream reported an error (rate_limit_exceeded).'),
    ({'type': 'error', 'code': 'Private payload', 'message': 'Private payload'},
     'Codex Responses stream reported an error.'),
    ({'type': 'response.refusal.delta', 'delta': 'Private payload'},
     'Codex Responses refused to provide a completion.'),
    ({'type': 'response.refusal.done', 'refusal': 'Private payload'},
     'Codex Responses refused to provide a completion.'),
    (_completed([{'type': 'refusal', 'refusal': 'Private payload'}]),
     'Codex Responses refused to provide a completion.'),
    (_completed(status='incomplete', incomplete_details={'reason': 'content_filter'}),
     'Codex Responses stream incomplete (content_filter).'),
    (_completed(status='failed', error={'code': 'Private payload'}),
     'Codex Responses stream failed.'),
    (_completed(status='in_progress'), 'Codex Responses stream had an invalid completed response.'),
    (_completed([{'type': 'reasoning_text', 'text': 'Private payload'}]),
     'Codex Responses returned an empty completion.'),
])
def test_unsuccessful_streams_have_distinct_safe_errors(monkeypatch, mode, terminal, message):
    with pytest.raises(RuntimeError) as error:
        if mode == 'stream':
            list(_stream(monkeypatch, [terminal]))
        else:
            codex._parse_responses_sse(_wire([terminal]))
    assert str(error.value) == message
    assert 'Private payload' not in str(error.value)


@pytest.mark.parametrize('mode', ['stream', 'buffered'])
@pytest.mark.parametrize('prefix', [[], [{'type': 'response.output_text.delta', 'delta': 'Partial answer'}],
    [{'type': 'response.output_text.done', 'text': 'Partial answer'}]])
def test_done_or_eof_cannot_replace_completed_terminal(monkeypatch, mode, prefix):
    with pytest.raises(RuntimeError, match='ended before response.completed'):
        if mode == 'stream':
            list(_stream(monkeypatch, prefix))
        else:
            codex._parse_responses_sse(_wire(prefix))


@pytest.mark.parametrize('terminal', [None,
    {'type': 'response.failed', 'response': {'status': 'failed'}},
    {'type': 'response.incomplete', 'response': {'status': 'incomplete'}},
    _completed([{'type': 'refusal', 'refusal': 'Private payload'}]),
])
def test_provider_never_marks_partial_output_completed_or_retries(monkeypatch, tmp_path, terminal):
    events = [{'type': 'response.output_text.delta', 'delta': 'Partial answer'}]
    if terminal is not None:
        events.append(terminal)
    calls = []

    def transport(*args, **kwargs):
        calls.append(True)
        return _stream(monkeypatch, events)

    tokens = tmp_path / 'tokens.json'
    tokens.write_text(json.dumps({'access_token': 'fake', 'refresh_token': 'fake',
        'account_id': 'fake', 'expires_at': 9999999999}), encoding='utf-8')
    provider = codex.OpenAICodexProvider(token_path=tokens, http_post_stream=transport)
    iterator = provider.stream_with_metrics('Mocked prompt')
    assert next(iterator) == {'type': 'delta', 'text': 'Partial answer'}
    with pytest.raises(RuntimeError):
        next(iterator)
    assert calls == [True]
