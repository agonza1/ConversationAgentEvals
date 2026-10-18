"""Hosted Responses transport. Fixed destination; only the authenticated user's key."""
import time
import httpx
from app.services.user_credentials import openai_key


class UserOpenAIProvider:
    provider_id = 'openai_api_key'

    def status(self):
        connected = bool(openai_key())
        return {'id': 'openai', 'provider': self.provider_id,
                'status': 'connected' if connected else 'disconnected',
                'email': None, 'account_id': None, 'plan_type': None,
                'message': 'Using your API key. Usage is billed to your OpenAI API account.' if connected
                else 'Add your OpenAI API key in Settings. No service credentials or ChatGPT account are used.'}

    def start_oauth(self):
        raise ValueError('Hosted mode uses your API key. Local ChatGPT OAuth is disabled.')

    def disconnect(self):
        from app.security import PrivateDocuments
        records = PrivateDocuments('credentials')
        if 'openai' in records: del records['openai']
        return self.status()

    def list_models(self):
        return {'provider': self.provider_id, 'status': self.status()['status'], 'source': 'configured',
                'default_model': 'gpt-4.1-mini', 'models': [{'id': model} for model in ('gpt-4.1-mini', 'gpt-4.1', 'gpt-5.4-mini')]}

    def complete_with_metrics(self, prompt, *, model_name=None):
        key = openai_key()
        if not key: raise ValueError('Add your OpenAI API key in Settings before running a model.')
        model = (model_name or 'gpt-4.1-mini').removeprefix('openai/')
        if '/' in model or not model.strip(): raise ValueError('Choose an OpenAI model ID.')
        started = time.perf_counter()
        try:
            response = httpx.post('https://api.openai.com/v1/responses',
                headers={'Authorization': f'Bearer {key}'},
                json={'model': model, 'input': prompt, 'store': False, 'max_output_tokens': 4096}, timeout=60)
            response.raise_for_status()
            result = response.json()
            text = '\n'.join(part.get('text', '') for item in result.get('output', [])
                             if item.get('type') == 'message' for part in item.get('content', [])
                             if part.get('type') == 'output_text')
            if not text: raise ValueError('Empty model response')
        except (httpx.HTTPError, ValueError, KeyError):
            raise RuntimeError('OpenAI request failed. Check your API key, model access, and API billing. No fallback was used.') from None
        return {'text': text, 'ttft_ms': None, 'total_ms': round((time.perf_counter() - started) * 1000, 3)}

    def complete(self, prompt, *, model_name=None):
        return self.complete_with_metrics(prompt, model_name=model_name)['text']
