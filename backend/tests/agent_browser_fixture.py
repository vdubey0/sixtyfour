"""Offline browser fixture, never imported by the production app.

Run: python -m uvicorn backend.tests.agent_browser_fixture:app --port 8001
Use SIXTYFOUR_API_TARGET=http://127.0.0.1:8001 for a separate Vite instance.
Only local CSV tools are available; all paid transports are disabled.
"""
import asyncio
from backend.main import app
from backend.agent import routes, graph, tools
from backend.agent.jev import Selection
from backend.tests.test_agent import decision, plan, tool
from backend.blocks.sixtyfour import SixtyfourClient


routes.configuration = lambda: {'configured': True, 'model': 'offline-test-fixture'}
routes.jev_configuration = lambda: {'configured': True, 'model': 'offline-jev-fixture'}
real_get = tools.get_block_function


def local_only(kind):
    if kind not in ('filter', 'inspect_dataset', 'deduplicate_rows'):
        raise ValueError('Offline fixture only permits local CSV tools.')
    return real_get(kind)


tools.get_block_function = local_only


def no_remote_requests(*args, **kwargs):
    raise RuntimeError('Paid API requests are disabled in the offline fixture.')


SixtyfourClient.request = no_remote_requests


async def scripted_decide(self, prompt, state, timeout):
    await asyncio.sleep(0.5)
    if 'wait' in state['conversation'][0]['content'].lower():
        await asyncio.sleep(50)
    if state['dataset'] is None:
        if len(state['conversation']) == 1:
            return decision('clarify', message='This offline test needs a CSV. Would you like to start a new chat and attach the synthetic example?')
        return decision('partial', message='Start a new chat and attach inputs/example_workflow.csv to try the local tools.')
    if state['phase'] == 'plan':
        result = plan(mode='unknown', min_rows=2, max_rows=2, unique_by=['email'],
                      description='Two distinct contacts with nonempty emails from the uploaded CSV.')
        result.plan = ['Keep rows with an email address.', 'Remove duplicate emails and export the contact list.']
        result.message = 'I’ll clean the uploaded contacts and keep one row per email.'
        return result
    if state['dataset']['missing_counts'].get('email'):
        return tool('filter', {'rules': [{'column': 'email', 'operator': 'is_present'}]},
                    message='The CSV has a missing email. I’ll keep rows with an email before removing duplicates.')
    return tool('deduplicate_rows', {'key_columns': ['email']},
                message='Three rows have emails. I’ll remove the duplicate email to produce the final contact list.')


graph.Planner.decide = scripted_decide


async def scripted_choose(self, state, options, timeout):
    await asyncio.sleep(0.5)
    return Selection('filter' if 'filter' in options else 'deduplicate_rows', .99)


graph.JevRouter.choose = scripted_choose
