"""Run the pinned native ASSERT test_set stage through CAE's selected provider.

The transport supplies only text generation. It never binds a target connector,
changes a target system prompt, invokes ASSERT inference or runs a judge.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import litellm
from litellm import CustomLLM, ModelResponse
from litellm.llms.custom_llm import CustomLLMError
from pydantic import BaseModel, ConfigDict, StrictStr

from app.integrations.assert_runtime import native_test_set_runtime
from app.services.editable_assert_spec import _complete_generation
from app.services.spec_generation_settings import generation_settings


class _NativeSeed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: StrictStr
    description: StrictStr
    system_prompt: StrictStr


class _NativeResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    test_set: list[_NativeSeed]


def _validate_response(text: str, options: dict) -> None:
    schema = ((options.get('response_format') or {}).get('json_schema') or {}).get('schema')
    if not isinstance(schema, dict):
        raise ValueError('ASSERT generation requires its native structured response schema.')
    items = schema['properties']['test_set']
    parsed = _NativeResponse.model_validate_json(text)
    if not items['minItems'] <= len(parsed.test_set) <= items['maxItems']:
        raise ValueError('Generated cases do not match the exact native batch count.')
    if any(not case.title.strip() or not case.description.strip() for case in parsed.test_set):
        raise ValueError('Generated cases need a title and opening request.')


class CAEGenerationTransport(CustomLLM):
    def __init__(self, configuration: dict):
        self.configuration = configuration
        self.calls = 0

    def completion(self, model: str, messages: list, optional_params: dict | None = None, **kwargs):
        try:
            settings = generation_settings()
            if settings['provider'] != self.configuration['provider'] or settings['effective_model'] != self.configuration['model']:
                raise ValueError('Generation provider changed during this request.')
            self.calls += 1
            if self.calls > self.configuration['max_model_calls']:
                raise ValueError('Generation call limit reached.')
            if any(message.get('role') not in {'system', 'user', 'assistant'}
                   or not isinstance(message.get('content'), str) or message.get('tool_calls') for message in messages):
                raise ValueError('Only text case generation is permitted.')
            prompt = '\n\n'.join(f'{message["role"].upper()}:\n{message["content"]}' for message in messages)
            text, provider, actual_model = _complete_generation(prompt,
                expected_provider=self.configuration['provider'], expected_model=self.configuration['model'])
            expected_provider = 'openai_api_key' if settings['provider'] == 'openai_api_key' else 'openai_codex'
            if provider != expected_provider or actual_model != self.configuration['model']:
                raise ValueError('Generation provider changed during this request.')
            _validate_response(text, optional_params or {})
            return ModelResponse(model='cae_generation/' + model, choices=[{'index': 0,
                'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}])
        except Exception:
            # Do not retry ambiguous provider errors or expose provider response bodies.
            raise CustomLLMError(status_code=400, message='CAE generation transport failed; no fallback used.') from None

    async def acompletion(self, model: str, messages: list, optional_params: dict | None = None, **kwargs):
        # Stage concurrency is pinned to one: no mutable budget/provider races.
        return await asyncio.to_thread(self.completion, model=model, messages=messages, optional_params=optional_params, **kwargs)


async def run(request_path: Path):
    runtime = native_test_set_runtime()
    from litellm.utils import custom_llm_setup

    request = json.loads(request_path.read_text(encoding='utf-8'))
    root = request_path.parent
    configuration = request['configuration']
    transport = CAEGenerationTransport(configuration)
    litellm.custom_provider_map = [row for row in litellm.custom_provider_map if row['provider'] != 'cae_generation'] + [
        {'provider': 'cae_generation', 'custom_handler': transport}]
    custom_llm_setup()
    records = []
    errors = 0
    for category in request['taxonomy']['behavior_categories']:
        taxonomy = {**request['taxonomy'], 'behavior_categories': [category]}
        taxonomy_path = root / f'{category["name"]}-taxonomy.json'
        taxonomy_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding='utf-8')
        output_path = root / f'{category["name"]}-test_set.jsonl'
        result = await runtime.run_test_set(taxonomy_path=str(taxonomy_path), save_path=str(output_path),
            context=request['context'], prompt={'model': 'cae_generation/' + configuration['model'],
                'sample_size': configuration['samples_per_behavior'], 'temperature': None,
                # Cancelling a to_thread call cannot stop its provider request. The
                # parent kills this whole process at the bounded wall-clock limit.
                'timeout_s': None,
                'sampling': {'method': 'stratified', 'stratify_by': ['behavior', 'variant']}},
            scenario=None, target=None, tool_source='runtime',
            stratification={'variant': configuration['variants']}, seed=0, concurrency=1)
        errors += result['errored_count']
        records.extend(json.loads(line) for line in output_path.read_text(encoding='utf-8').splitlines() if line.strip())
    # Each per-behavior stage starts IDs at one. Renormalize once over the whole import.
    records = runtime.normalize_test_case_rows(records)
    runtime.write_jsonl(root / 'test_set.jsonl', records)
    (root / 'summary.json').write_text(json.dumps({'saved_count': len(records), 'errored_count': errors,
        'model_calls': transport.calls}), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]).resolve()))
