"""Register a model transport, then execute the unmodified upstream ASSERT CLI."""
from __future__ import annotations

import asyncio
import os
import runpy
from typing import Any

import litellm
from litellm import CustomLLM, ModelResponse
from litellm.llms.custom_llm import CustomLLMError

from app.services.llm_providers.chatgpt_plan import (
    ChatGPTPlanError, MODEL_PREFIX, RESOURCE, get_chatgpt_provider,
)


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
        kind = event.get('type')
        if kind in {'error', 'response.failed', 'response.incomplete'}:
            code = (event.get('response', {}).get('error') or event.get('error') or {}).get('code')
            if code in {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable'}:
                raise ChatGPTPlanError('ChatGPT plan usage is unavailable or its limit was reached. Check ChatGPT Settings; no API-key fallback was used.')
            raise ChatGPTPlanError('ChatGPT inference failed or was incomplete; the recorded agent result is unchanged.')
        if kind == 'response.completed':
            completed = event.get('response')
    if not isinstance(completed, dict) or completed.get('status') != 'completed':
        raise ChatGPTPlanError('ChatGPT stream ended without completed inference; no review was saved.')
    return completed


class ChatGPTAssertTransport(CustomLLM):
    def completion(self, model: str, messages: list, optional_params: dict | None = None, **kwargs: Any) -> ModelResponse:
        try:
            provider = get_chatgpt_provider()
            token = provider.access_token(os.getenv('CAE_CHATGPT_EXPECTED_BINDING'))
            body = responses_request(model, messages, optional_params or {})
            with provider.client.stream('POST', f'{RESOURCE}/responses', json=body,
                    headers={'Authorization': f'Bearer {token}'},
                    timeout=float(os.getenv('ASSERT_JUDGE_TIMEOUT_SECONDS', '300'))) as stream:
                if stream.status_code != 200:
                    raise ChatGPTPlanError('ChatGPT inference was rejected. Reconnect or check account/model limits; no API-key fallback was used.')
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
        except Exception as exc:
            # Prevent automatic retries of ambiguous streams or plan-admission failures.
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
