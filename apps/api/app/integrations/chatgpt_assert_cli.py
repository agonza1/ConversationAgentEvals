"""Register a model transport, then execute the unmodified upstream ASSERT CLI."""
from __future__ import annotations

import asyncio
import os
import re
import runpy
from typing import Any

import litellm
from litellm import CustomLLM, ModelResponse
from litellm.llms.custom_llm import CustomLLMError

from app.services.llm_providers.chatgpt_plan import (
    ChatGPTPlanError, MODEL_PREFIX, RESOURCE, get_chatgpt_provider,
)


class _AdmissionFailure(ChatGPTPlanError):
    def __init__(self, response: Any) -> None:
        self.status_code = response.status_code
        code = None
        param = None
        shape = 'non_json'
        try:
            payload = response.read()  # Error bodies only: inference streaming has not started.
            import json
            payload = json.loads(payload)
            shape = 'json'
            if isinstance(payload, dict):
                error = payload.get('error')
                shape = 'error_object' if isinstance(error, dict) else 'detail' if 'detail' in payload else 'json'
                code = error.get('code') if isinstance(error, dict) else error
                param = error.get('param') if isinstance(error, dict) else None
        except Exception:
            pass
        self.provider_code = code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', code) else None
        self.provider_param = param if isinstance(param, str) and re.fullmatch(r'[A-Za-z0-9_.\[\]-]{1,120}', param) else None
        request_id = response.headers.get('x-request-id', '')
        self.request_id = request_id if re.fullmatch(r'req_[A-Za-z0-9_-]{1,160}', request_id) else None
        if self.provider_code == 'subscription_sharing_user_not_eligible':
            advice = 'ChatGPT plan use is unavailable for this account/workspace or policy. Check eligibility; do not loop through sign-in.'
        elif self.provider_code == 'subscription_sharing_unsupported_capability':
            advice = 'The configured model or capability is unsupported. Inspect the integration; do not repeat the same invalid request.'
        elif self.status_code == 429:
            advice = 'ChatGPT plan requests are rate/usage limited. Pause requests and check ChatGPT Settings usage; no reset time is assumed.'
        elif self.status_code >= 500:
            advice = 'ChatGPT service or routing is temporarily unavailable. Credentials were preserved; retry later with bounded backoff.'
        elif self.status_code == 401:
            advice = 'ChatGPT identity or direct permission was not accepted. Check the selected account and scopes; reconnect after confirmed revocation.'
        elif self.status_code == 403:
            advice = 'ChatGPT admission was denied by policy, region or permission checks. Verify the account grant and integration.'
        else:
            advice = 'ChatGPT rejected this request. Inspect the configured model and supported fields.'
        details = f'HTTP {self.status_code}; body={shape}'
        if self.provider_code: details += f'; code={self.provider_code}'
        if self.provider_param: details += f'; param={self.provider_param}'
        if self.request_id: details += f'; request_id={self.request_id}'
        super().__init__(f'{advice} [{details}] No API-key fallback was used.')


def responses_request(model: str, messages: list, options: dict) -> dict:
    """Only supported plan-use fields; no token cap/temperature/backend endpoints."""
    from app.services.llm_providers.chatgpt_plan import validate_model
    body: dict[str, Any] = {'model': validate_model(model), 'store': False, 'stream': True, 'input': []}
    for message in messages:
        role = message.get('role')
        if role not in {'system', 'developer', 'user', 'assistant'} or message.get('tool_calls'):
            raise ChatGPTPlanError('The ChatGPT ASSERT transport accepts text judging only, without executable tools.')
        content = message.get('content')
        if not isinstance(content, str):
            if not isinstance(content, list) or any(not isinstance(part, dict)
                    or part.get('type') not in {'text', 'input_text'} or not isinstance(part.get('text'), str)
                    for part in content):
                raise ChatGPTPlanError('The ChatGPT ASSERT transport requires text evidence.')
            content = '\n'.join(part['text'] for part in content)
        body['input'].append({'role': 'developer' if role == 'system' else role, 'content': content})
    format_ = options.get('response_format') or {}
    if format_.get('type') == 'json_schema':
        body['text'] = {'format': {'type': 'json_schema', **format_['json_schema']}}
    elif format_.get('type') == 'json_object':
        body['text'] = {'format': {'type': 'json_object'}}
    if options.get('reasoning_effort'):
        body['reasoning'] = {'effort': options['reasoning_effort']}
    return body


def collect_response(lines: Any) -> dict:
    """Never return partial text on a failed/incomplete/interrupted stream."""
    import json
    completed = None
    deltas: list[str] = []
    messages: list[dict] = []
    refused = False
    for line in lines:
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if not data or data == '[DONE]':
            continue
        try:
            event = json.loads(data)
        except ValueError as exc:
            raise ChatGPTPlanError('ChatGPT returned an invalid response stream; no review was saved.') from exc
        if not isinstance(event, dict):
            raise ChatGPTPlanError('ChatGPT returned an invalid response event; no review was saved.')
        kind = event.get('type')
        if kind == 'response.output_text.delta':
            if not isinstance(event.get('delta'), str):
                raise ChatGPTPlanError('ChatGPT returned invalid streamed text; no review was saved.')
            deltas.append(event['delta'])
        if kind in {'response.refusal.delta', 'response.refusal.done'}:
            refused = True
        if kind == 'response.output_item.done':
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'message':
                messages.append(item)
        if kind in {'error', 'response.failed', 'response.incomplete'}:
            code = (event.get('response', {}).get('error') or event.get('error') or {}).get('code')
            if code in {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable'}:
                raise ChatGPTPlanError('ChatGPT plan usage is unavailable or its limit was reached. Check ChatGPT Settings; no API-key fallback was used.')
            raise ChatGPTPlanError('ChatGPT inference failed or was incomplete; the recorded agent result is unchanged.')
        if kind == 'response.completed':
            completed = event.get('response')
    if not isinstance(completed, dict) or completed.get('status') != 'completed':
        raise ChatGPTPlanError('ChatGPT stream ended without completed inference; no review was saved.')
    output = completed.get('output') or []
    for item in [*output, *messages]:
        if isinstance(item, dict) and item.get('type') == 'message':
            refused |= any(part.get('type') == 'refusal' for part in item.get('content') or []
                           if isinstance(part, dict))
    if refused:
        raise ChatGPTPlanError('ChatGPT refused the judge input; no review was saved.')
    # Plan-use streams may deliver the text in events without repeating it in
    # the terminal snapshot. Only expose it AFTER completed inference, never on
    # failure/truncation. Prefer complete snapshots to avoid duplicating deltas.
    def has_text(items: list) -> bool:
        return any(isinstance(item, dict) and item.get('type') == 'message'
                   and any(isinstance(part, dict) and part.get('type') == 'output_text'
                           and isinstance(part.get('text'), str) and part['text']
                           for part in item.get('content') or []) for item in items)
    if not has_text(output):
        if has_text(messages):
            completed = {**completed, 'output': [*output, *messages]}
        elif deltas:
            completed = {**completed, 'output': [*output, {'type': 'message',
                'content': [{'type': 'output_text', 'text': ''.join(deltas)}]}]}
    return completed


class ChatGPTAssertTransport(CustomLLM):
    def completion(self, model: str, messages: list, optional_params: dict | None = None, **kwargs: Any) -> ModelResponse:
        try:
            from app.services.upstream_assert_judge import _positive_int_env, DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS
            provider = get_chatgpt_provider()
            token = provider.access_token(os.getenv('CAE_CHATGPT_EXPECTED_BINDING'))
            body = responses_request(model, messages, optional_params or {})
            with provider.client.stream('POST', f'{RESOURCE}/responses', json=body,
                    headers={'Authorization': f'Bearer {token}'},
                    timeout=_positive_int_env('ASSERT_JUDGE_TIMEOUT_SECONDS', DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS)) as stream:
                if stream.status_code != 200:
                    raise _AdmissionFailure(stream)
                result = collect_response(stream.iter_lines())
            text = ''.join(part.get('text', '') for item in result.get('output', [])
                          if item.get('type') == 'message' for part in item.get('content', [])
                          if part.get('type') == 'output_text')
            if not text:
                raise ChatGPTPlanError('ChatGPT completed without judge text (possibly a refusal); no review was saved.')
            usage = result.get('usage') or {}
            return ModelResponse(model=MODEL_PREFIX + model, id=result.get('id'),
                choices=[{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
                usage={'prompt_tokens': usage.get('input_tokens', 0),
                       'completion_tokens': usage.get('output_tokens', 0), 'total_tokens': usage.get('total_tokens', 0)})
        except _AdmissionFailure as exc:
            # Preserve pre-stream status/code for upstream ASSERT classification
            # and its bounded backoff. No inference stream was accepted here.
            raise CustomLLMError(status_code=exc.status_code, message=str(exc)) from None
        except Exception as exc:
            # Never automatically retry an ambiguous/incomplete accepted stream.
            safe = str(exc) if isinstance(exc, ChatGPTPlanError) else 'ChatGPT transport failed; no review was saved or API-key fallback used.'
            raise CustomLLMError(status_code=400, message=safe) from None

    async def acompletion(self, model: str, messages: list, optional_params: dict | None = None, **kwargs: Any) -> ModelResponse:
        return await asyncio.to_thread(self.completion, model=model, messages=messages, optional_params=optional_params)


def register_transport() -> None:
    from litellm.utils import custom_llm_setup
    litellm.custom_provider_map = [row for row in litellm.custom_provider_map if row['provider'] != 'chatgpt_plan'] + [
        {'provider': 'chatgpt_plan', 'custom_handler': ChatGPTAssertTransport()}]
    custom_llm_setup()


if __name__ == '__main__':
    register_transport()
    runpy.run_module('assert_ai.cli', run_name='__main__')
