"""One bounded model request; all retry/budget policy belongs to the graph runner."""
import json
import httpx
from backend.blocks.sixtyfour import setting
from backend.agent.schemas import Decision


class ModelError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def configuration():
    key = setting('OPENAI_API_KEY', '')
    model = setting('AGENT_MODEL', '')
    return {'configured': bool(key and key != 'replace_with_your_key' and model),
            'model': model or None}


class Planner:
    async def decide(self, system, observation, timeout):
        if not configuration()['configured']:
            raise ModelError('Set OPENAI_API_KEY and AGENT_MODEL in backend/.env to use agent mode.')
        # One strict function is the graph's decision envelope. Arbitrary tool JSON
        # is carried as text and validated by the existing catalog before execution.
        payload = {
            'model': setting('AGENT_MODEL'), 'store': False,
            'instructions': system,
            'input': json.dumps(observation, ensure_ascii=False, allow_nan=False),
            'tools': [{'type': 'function', 'name': 'agent_action',
                       'description': 'Choose the next graph action or stop.',
                       'parameters': Decision.model_json_schema(), 'strict': True}],
            'tool_choice': {'type': 'function', 'name': 'agent_action'},
            'parallel_tool_calls': False, 'max_output_tokens': 4000,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post('https://api.openai.com/v1/responses',
                    headers={'Authorization': 'Bearer ' + setting('OPENAI_API_KEY')}, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ModelError('Planner connection failed (' + type(exc).__name__ + ').', True) from exc
        if response.status_code >= 400:
            # Never reflect arbitrary provider payloads or credentials to the UI.
            raise ModelError('Planner HTTP %s. Check model access, credentials, and quota.' % response.status_code,
                             response.status_code == 429 or response.status_code >= 500)
        body = response.json()
        calls = [item for item in body.get('output', []) if item.get('type') == 'function_call']
        if body.get('status') != 'completed' or len(calls) != 1 or calls[0].get('name') != 'agent_action':
            raise ValueError('Planner did not return one complete agent action.')
        return Decision.model_validate_json(calls[0]['arguments'])
