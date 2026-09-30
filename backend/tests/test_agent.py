"""Offline adversarial loop, API, and model transport tests. No paid API calls."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
import pandas as pd
from fastapi.testclient import TestClient
from langgraph.errors import GraphRecursionError
from backend.agent.graph import AgentRun, execute_run
from backend.agent.limits import Limits
from backend.agent.model import Planner, ModelError
from backend.agent.prompts import system_prompt
from backend.agent.schemas import Contract, Decision
from backend.agent.tools import check_contract, fingerprint, prepare
from backend.blocks.sixtyfour import APIError
from backend.engine.compatibility import DatasetSchema


def contract(**changes):
    values = dict(description='Find ten people with emails.', mode='people', min_rows=10,
                  max_rows=None, required_columns=['email'], unique_by=[], conditions=[], unresolved_requirements=[])
    values.update(changes)
    return Contract(**values)


def decision(action='tool', **changes):
    values = dict(action=action, message='A brief progress update.', plan=None, contract=None,
                  initial_dataset_mode=None, tool=None, arguments_json=None, input_dataset=None)
    values.update(changes)
    return Decision(**values)


def tool(name, arguments=None, **changes):
    return decision(tool=name, arguments_json=json.dumps(arguments or {}), **changes)


def plan(**changes):
    return decision('plan', plan=['Find people.', 'Complete the requested fields.'], contract=contract(**changes))


class ScriptedPlanner:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.observations = []
        self.prompts = []

    async def decide(self, prompt, state, timeout):
        self.prompts.append(prompt)
        self.observations.append(state)
        value = next(self.decisions)
        if isinstance(value, Exception):
            raise value
        return value


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.export_patch = patch('backend.engine.execution.ROOT', self.root)
        self.export_patch.start()

    def tearDown(self):
        self.export_patch.stop()
        self.temp.cleanup()

    def make_run(self, script, seeded=False, limits=None):
        run = AgentRun('Find ten people with emails.', limits=limits or Limits(), planner=ScriptedPlanner(script))
        run.loop = asyncio.get_running_loop()
        run.sink = asyncio.Queue()
        if seeded:
            df = pd.DataFrame([{'name': 'Ada', 'email': 'ada@example.com', '_row_id': 'seed:0'}])
            run.context.snapshots['seed'] = df
            run.context.schema_snapshots['seed'] = DatasetSchema('people', set(df.columns))
            run.current = 'seed'
            run.seen_data.add(fingerprint(df))
        return run

    async def execute(self, run):
        await execute_run(run)
        await asyncio.sleep(0)
        events = []
        while not run.sink.empty():
            events.append(run.sink.get_nowait())
        self.assertEqual(events[-1]['event'], 'complete')
        return events

    async def test_query_only_search_completes_and_exports(self):
        run = self.make_run([plan(min_rows=1), tool('find_prospects', {'query': 'Engineering leads'})])
        with patch('backend.agent.tools.get_block_function', return_value=lambda *_: pd.DataFrame([{'email': 'a@example.com'}])):
            events = await self.execute(run)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.tool_attempts, 1)
        self.assertEqual(run.model_requests, 2)
        self.assertTrue(run.final['downloads'])
        self.assertEqual([e['event'] for e in events if e['event'].startswith('tool_')], ['tool_started', 'tool_done'])
        self.assertEqual(len(list(self.root.rglob('*.csv'))), 1)

    async def test_excess_complete_candidates_selects_first_requested_and_exports(self):
        run = self.make_run([plan(min_rows=2, max_rows=2), tool('find_prospects', {'query': 'Engineering leads'})])
        rows = pd.DataFrame({'email': ['first@example.com', 'second@example.com', 'third@example.com']})
        with patch('backend.agent.tools.get_block_function', return_value=lambda *_: rows):
            await self.execute(run)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.data()['email'].tolist(), rows['email'].tolist()[:2])
        self.assertEqual(len(run.context.snapshots['step-1']), 3)
        self.assertEqual(run.model_requests, 2)
        self.assertEqual(run.final['row_count'], 2)
        exported = pd.read_csv(next(self.root.rglob('*.csv')))
        self.assertEqual(exported['email'].tolist(), rows['email'].tolist()[:2])

    async def test_excess_candidates_with_incomplete_prefix_are_not_selected(self):
        run = self.make_run([], seeded=True)
        run.contract = contract(min_rows=1, max_rows=1)
        run.context.snapshots['seed'] = pd.DataFrame({'email': [None, 'complete@example.com']})
        run.select_requested_rows()
        self.assertEqual(run.current, 'seed')
        self.assertFalse(run.checks()['satisfied'])

    async def test_minimum_count_does_not_truncate_candidates(self):
        run = self.make_run([], seeded=True)
        run.contract = contract(min_rows=1)
        run.context.snapshots['seed'] = pd.DataFrame({'email': ['a@example.com', 'b@example.com']})
        run.select_requested_rows()
        self.assertEqual(len(run.data()), 2)

    async def test_csv_filter_then_deduplicate_uses_real_tools(self):
        run = self.make_run([plan(mode='unknown', min_rows=2, max_rows=2, unique_by=['email']),
                             tool('filter', {'rules': [{'column': 'email', 'operator': 'is_present'}]}),
                             tool('deduplicate_rows', {'key_columns': ['email']})])
        run.upload_path = 'inputs/example_workflow.csv'
        events = await self.execute(run)
        self.assertEqual(run.status, 'completed', run.final)
        self.assertEqual(run.tool_attempts, 3)  # Automatic CSV load also counts.
        self.assertEqual(run.final['row_count'], 2)
        self.assertEqual(sum(e['event'] == 'tool_done' for e in events), 3)
        df = pd.read_csv(next(self.root.rglob('*.csv')))
        self.assertEqual(len(df), 2)

    async def test_twenty_tool_attempts_is_hard_limit(self):
        script = [plan(min_rows=100)] + [tool('find_prospects', {'query': 'query %s' % i}) for i in range(21)]
        run = self.make_run(script)
        def search(*_):
            return pd.DataFrame([{'email': '%s@example.com' % run.tool_attempts}])
        with patch('backend.agent.tools.get_block_function', return_value=search) as fn:
            await self.execute(run)
        self.assertEqual(run.reason, 'tool_limit')
        self.assertEqual(run.tool_attempts, 20)
        self.assertEqual(fn.call_count, 20)
        self.assertEqual(run.status, 'partial')
        self.assertTrue(run.final['downloads'])

    async def test_duplicate_action_rejected_before_execution(self):
        run = self.make_run([plan(), tool('inspect_dataset'), tool('inspect_dataset'), tool('inspect_dataset')], seeded=True)
        await self.execute(run)
        self.assertEqual(run.tool_attempts, 1)
        self.assertEqual(run.reason, 'invalid_decisions')
        self.assertIn('Duplicate action', run.history[-1]['error'])

    async def test_no_progress_three_distinct_actions(self):
        filters = [tool('filter', {'rules': [{'column': 'email', 'operator': 'contains', 'value': v}]})
                   for v in ('ada', '@', 'example')]
        run = self.make_run([plan(), *filters], seeded=True)
        await self.execute(run)
        self.assertEqual(run.tool_attempts, 3)
        self.assertEqual(run.reason, 'no_progress')

    async def test_two_tool_failures_stop(self):
        run = self.make_run([plan(), tool('find_prospects', {'query': 'one'}), tool('find_prospects', {'query': 'two'})])
        with patch('backend.agent.tools.get_block_function', return_value=Mock(side_effect=APIError('Remote job failed'))):
            await self.execute(run)
        self.assertEqual(run.reason, 'tool_failures')
        self.assertEqual(run.tool_attempts, 2)
        self.assertEqual(run.status, 'failed')

    async def test_all_failed_row_results_count_as_tool_failure(self):
        run = self.make_run([plan(), tool('find_prospects', {'query': 'one'}), tool('find_prospects', {'query': 'two'})])
        frame = pd.DataFrame([{'email': None, '_status': 'failed'}])
        frame.attrs['failed_rows'] = 1
        with patch('backend.agent.tools.get_block_function', return_value=lambda *_: frame.copy()):
            await self.execute(run)
        self.assertEqual(run.reason, 'tool_failures')
        self.assertEqual(run.failed_rows, 2)

    async def test_auth_credit_permission_stop_on_first_failure(self):
        for status in (401, 402, 403):
            with self.subTest(status=status):
                run = self.make_run([plan(), tool('find_prospects', {'query': 'one'})])
                with patch('backend.agent.tools.get_block_function', return_value=Mock(side_effect=APIError('No access', status))):
                    await self.execute(run)
                self.assertEqual(run.reason, 'provider_access')
                self.assertEqual(run.tool_attempts, 1)

    async def test_invalid_arguments_are_decisions_not_executions(self):
        run = self.make_run([plan(), tool('find_email', {'not_an_option': True}), tool('made_up_tool')], seeded=True)
        await self.execute(run)
        self.assertEqual(run.reason, 'invalid_decisions')
        self.assertEqual(run.tool_attempts, 0)

    async def test_premature_success_and_weakened_contract_rejected(self):
        run = self.make_run([plan(), decision('finish'), decision('finish', contract=contract(min_rows=0))], seeded=True)
        await self.execute(run)
        self.assertEqual(run.reason, 'invalid_decisions')
        self.assertEqual(run.contract.min_rows, 10)

    async def test_clarification_preserves_budget_until_thirty_requests(self):
        run = self.make_run([decision('clarify', message='Which industry?')] * 31)
        for index in range(30):
            await self.execute(run)
            self.assertEqual(run.model_requests, index + 1)
            if index < 29:
                self.assertEqual(run.status, 'needs_input')
                run.status = 'running'
                run.conversation.append({'role': 'user', 'content': 'Software'})
        self.assertEqual(run.reason, 'model_limit')
        self.assertNotEqual(run.status, 'needs_input')

    async def test_model_retry_counts_and_stops_at_budget(self):
        run = self.make_run([ModelError('Transient', True), plan()], limits=Limits(model_requests=1))
        await self.execute(run)
        self.assertEqual(run.reason, 'model_limit')
        self.assertEqual(run.model_requests, 1)

    async def test_model_transport_retries_only_once(self):
        run = self.make_run([ModelError('Transient', True)] * 3)
        await self.execute(run)
        self.assertEqual(run.reason, 'model_error')
        self.assertEqual(run.model_requests, 2)

    async def test_model_timeout_is_bounded_without_global_deadline(self):
        run = self.make_run([], limits=Limits(model_timeout=1))
        async def hang(*_):
            await asyncio.sleep(100)
        run.planner.decide = hang
        await asyncio.wait_for(self.execute(run), timeout=4)
        self.assertEqual(run.reason, 'model_error')
        self.assertEqual(run.model_requests, 2)

    async def test_stop_during_model_wait_prevents_tools_and_exports(self):
        run = self.make_run([], seeded=True)
        async def hang(*_):
            await asyncio.sleep(100)
        run.planner.decide = hang
        task = asyncio.create_task(self.execute(run))
        await asyncio.sleep(0.05)
        run.context.cancelled.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(run.status, 'cancelled')
        self.assertEqual(run.tool_attempts, 0)
        self.assertTrue(run.final['downloads'])

    async def test_stop_during_tool_preserves_last_snapshot(self):
        run = self.make_run([plan(), tool('find_phone')], seeded=True)
        def long_job(df, config, context):
            context.wait(100)
            return df
        with patch('backend.agent.tools.get_block_function', return_value=long_job):
            task = asyncio.create_task(self.execute(run))
            while not run.tool_attempts:
                await asyncio.sleep(0.01)
            run.context.cancelled.set()
            await asyncio.wait_for(task, timeout=1)
        self.assertEqual(run.status, 'cancelled')
        self.assertEqual(run.current, 'seed')
        self.assertTrue(run.final['downloads'])

    async def test_metadata_does_not_replace_people_dataset(self):
        run = self.make_run([plan(), tool('get_search_fields'), decision('partial', message='No more useful searches.')], seeded=True)
        with patch('backend.agent.tools.get_block_function', return_value=lambda *_: pd.DataFrame([{'name': 'industry'}])):
            await self.execute(run)
        self.assertEqual(run.current, 'seed')
        self.assertTrue(run.metadata)
        self.assertEqual(run.final['columns'], ['name', 'email'])

    async def test_dataset_cycles_and_bookkeeping_are_not_progress(self):
        one = pd.DataFrame([{'email': 'a@example.com', '_history': 'old'}])
        two = one.copy(); two['_history'] = 'new'; two['_row_id'] = 'other'
        self.assertEqual(fingerprint(one), fingerprint(two))
        run = self.make_run([plan(), tool('find_prospects', {'query': 'one'}),
                             tool('find_prospects', {'query': 'two'}),
                             tool('find_prospects', {'query': 'three'}),
                             tool('find_prospects', {'query': 'four'})])
        with patch('backend.agent.tools.get_block_function', return_value=lambda *_: two.copy()):
            await self.execute(run)
        self.assertEqual(run.reason, 'no_progress')
        self.assertEqual(run.tool_attempts, 4)  # First result was new; next three were not.

    async def test_export_failure_does_not_restart_or_hide_preview(self):
        run = self.make_run([decision('partial', message='Cannot proceed.')], seeded=True)
        with patch('backend.agent.graph.run_save', side_effect=OSError('disk full')) as save:
            await self.execute(run)
        self.assertEqual(save.call_count, 1)
        self.assertIn('disk full', run.final['export_error'])
        self.assertEqual(run.final['row_count'], 1)

    async def test_contract_checks_verified_fields_and_duplicates(self):
        c = contract(min_rows=1, max_rows=2, unique_by=['email'], conditions=[
            {'column': 'email_status', 'operator': 'eq', 'value_json': '"valid"'}])
        df = pd.DataFrame([{'email': 'a@example.com', 'email_status': 'unknown'}] * 2)
        result = check_contract(c, df, 'people')
        self.assertFalse(result['satisfied'])
        self.assertTrue(any('duplicate' in issue for issue in result['issues']))
        self.assertTrue(any('email_status' in issue for issue in result['issues']))

    async def test_prompt_contains_actual_limits_and_counters(self):
        run = self.make_run([decision('partial', message='Cannot proceed.')], limits=Limits(tool_attempts=4, model_requests=7))
        await self.execute(run)
        prompt = run.planner.prompts[0]
        self.assertIn('at most', prompt.lower())
        self.assertIn('4 tool attempts and 7 model requests', prompt)
        self.assertIn('NO global run time limit', prompt)
        self.assertEqual(run.planner.observations[0]['budgets']['models_remaining'], 6)

    async def test_recursion_backstop_exports_without_another_model_call(self):
        run = self.make_run([], seeded=True)
        graph = Mock()
        graph.ainvoke = AsyncMock(side_effect=GraphRecursionError('loop'))
        with patch('backend.agent.graph.build_graph', return_value=graph):
            await self.execute(run)
        self.assertEqual(run.reason, 'graph_limit')
        self.assertEqual(run.model_requests, 0)
        self.assertTrue(run.final['downloads'])

    async def test_empty_filtered_result_keeps_schema_and_download(self):
        run = self.make_run([plan(min_rows=0, conditions=[
            {'column': 'email', 'operator': 'eq', 'value_json': '"absent@example.com"'}]),
            tool('filter', {'rules': [{'column': 'email', 'operator': 'eq', 'value': 'absent@example.com'}]})], seeded=True)
        await self.execute(run)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.final['row_count'], 0)
        self.assertIn('email', run.final['columns'])
        self.assertTrue(run.final['downloads'])

    async def test_oversized_tool_output_preserves_previous_dataset(self):
        run = self.make_run([plan(min_rows=2), tool('find_prospects', {'query': 'one', 'max_results': 2}),
                             decision('partial', message='Cannot complete within the row limit.')],
                            seeded=True, limits=Limits(max_rows=2))
        with patch('backend.agent.tools.get_block_function', return_value=lambda *_: pd.DataFrame([{'email': 'a'}] * 3)):
            await self.execute(run)
        self.assertEqual(run.current, 'seed')
        self.assertEqual(run.final['row_count'], 1)


class AgentAPITests(unittest.TestCase):
    def setUp(self):
        from backend.main import app
        from backend.agent.routes import RUNS
        RUNS.clear()
        self.client = TestClient(app)

    def test_setup_is_explicit_and_manual_catalog_still_available(self):
        with patch('backend.agent.routes.configuration', return_value={'configured': False, 'model': None}):
            self.assertEqual(self.client.get('/agent/config').json()['configured'], False)
            self.assertEqual(self.client.post('/agent/runs', json={'query': 'Find leads'}).status_code, 503)
        self.assertEqual(self.client.get('/blocks').status_code, 200)

    def test_upload_path_is_restricted(self):
        with patch('backend.agent.routes.configuration', return_value={'configured': True, 'model': 'test'}):
            response = self.client.post('/agent/runs', json={'query': 'Find leads', 'upload_path': 'outputs/private.csv'})
            self.assertEqual(response.status_code, 400)

    def test_stream_resume_and_terminal_cannot_restart(self):
        from backend.agent.routes import RUNS
        planner = ScriptedPlanner([decision('clarify', message='Which industry?'), decision('partial', message='No matching data.')])
        with patch('backend.agent.routes.configuration', return_value={'configured': True, 'model': 'test'}), \
             patch('backend.agent.graph.Planner.decide', side_effect=planner.decide):
            response = self.client.post('/agent/runs', json={'query': 'Find leads'})
            events = [json.loads(line) for line in response.text.splitlines()]
            self.assertEqual(events[-1]['status'], 'needs_input')
            run_id = events[0]['run_id']
            response = self.client.post('/agent/runs', json={'query': 'Software', 'run_id': run_id})
            self.assertEqual(json.loads(response.text.splitlines()[-1])['status'], 'failed')
            self.assertEqual(RUNS[run_id].model_requests, 2)
            self.assertEqual(self.client.post('/agent/runs', json={'query': 'Again', 'run_id': run_id}).status_code, 409)

    def test_cancel_paused_run_updates_recovery_and_blocks_resume(self):
        planner = ScriptedPlanner([decision('clarify', message='Which industry?')])
        with patch('backend.agent.routes.configuration', return_value={'configured': True, 'model': 'test'}), \
             patch('backend.agent.graph.Planner.decide', side_effect=planner.decide):
            response = self.client.post('/agent/runs', json={'query': 'Find leads'})
            run_id = json.loads(response.text.splitlines()[0])['run_id']
            self.assertEqual(self.client.post('/agent/runs/' + run_id + '/cancel').json()['status'], 'cancelled')
            self.assertEqual(self.client.get('/agent/runs/' + run_id).json()['status'], 'cancelled')
            self.assertEqual(self.client.post('/agent/runs', json={'query': 'Software', 'run_id': run_id}).status_code, 409)


class AgentConfigurationTests(unittest.TestCase):
    def test_redundant_lookups_are_rejected_but_missing_fields_and_refreshes_allowed(self):
        run = AgentRun('Complete missing contact information')
        df = pd.DataFrame({'name': ['Ada'], 'email': ['ada@example.com'],
                           'personal_email': ['ada@personal.com'], 'phone': ['123'],
                           'linkedin': ['https://linkedin.com/in/ada'], 'title': ['Engineer'],
                           'work_email': ['ada@example.com']})
        run.current = 'seed'
        run.context.snapshots['seed'] = df
        run.context.schema_snapshots['seed'] = DatasetSchema('people', set(df.columns))
        for name, args in (
            ('find_email', {}), ('find_email', {'mode': 'PERSONAL'}),
            ('find_email', {'input_mapping': {'name': 'name', 'email': 'work_email'}}),
            ('find_phone', {}), ('enrich_lead', {'struct': {'title': 'Job title'}}),
            ('enrich_linkedin', {'struct': {'title': 'Job title'}}),
        ):
            with self.subTest(tool=name, args=args):
                with self.assertRaisesRegex(ValueError, 'Unnecessary tool'):
                    prepare(run, tool(name, args))
        prepare(run, tool('enrich_lead', {'struct': {'company': 'Employer'}}))
        prepare(run, tool('enrich_lead', {'struct': {'title': 'Job title'}, 'overwrite': True}))
        prepare(run, tool('find_email', {'only_missing': False, 'verify_emails': True}))
        df.loc[0, 'phone'] = ' '
        prepare(run, tool('find_phone'))
        run.context.schema_snapshots['seed'].mode = 'companies'
        with self.assertRaisesRegex(ValueError, 'Unnecessary tool'):
            prepare(run, tool('research_companies', {'struct': {'name': 'Company name'}}))

    def test_environment_cannot_disable_or_raise_limits(self):
        with patch.dict(os.environ, {'AGENT_TOOL_ATTEMPTS': '999', 'AGENT_MODEL_REQUESTS': '0', 'AGENT_NO_PROGRESS': 'bad'}):
            limits = Limits.configured()
        self.assertEqual(limits.tool_attempts, 20)
        self.assertEqual(limits.model_requests, 1)
        self.assertEqual(limits.no_progress, 3)

    def test_metadata_and_tool_contracts_are_catalog_driven(self):
        run = AgentRun('query')
        with self.assertRaisesRegex(ValueError, 'catalog'):
            prepare(run, tool('save_csv'))
        self.assertIn('Authentication', system_prompt(Limits(), []))

    def test_per_endpoint_job_timeout_is_separate_and_finite(self):
        from backend.blocks.sixtyfour import SixtyfourClient
        from backend.engine.execution import RunContext
        with patch.dict(os.environ, {'SIXTYFOUR_API_KEY': 'test', 'SIXTYFOUR_SEARCH_JOB_TIMEOUT': '3600'}):
            client = SixtyfourClient(RunContext(lambda _: None))
            with patch.object(client, 'request', return_value={'task_id': 'job'}), \
                 patch('backend.blocks.sixtyfour.time.monotonic', side_effect=[0, 3601]):
                with self.assertRaisesRegex(APIError, 'Timed out'):
                    client.job('/search/start-deep-search', {}, search=True)


class ModelTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_strict_function_and_server_model(self):
        reply = decision('clarify', message='Which industry?')
        response = Mock(status_code=200)
        response.json.return_value = {'status': 'completed', 'output': [
            {'type': 'function_call', 'name': 'agent_action', 'arguments': reply.model_dump_json()}]}
        client = AsyncMock()
        client.post.return_value = response
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'AGENT_MODEL': 'test-model'}), \
             patch('backend.agent.model.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            result = await Planner().decide('prompt', {'budgets': {}}, 60)
        payload = client.post.call_args.kwargs['json']
        self.assertFalse(payload['store'])
        self.assertFalse(payload['parallel_tool_calls'])
        self.assertTrue(payload['tools'][0]['strict'])
        self.assertEqual(result, reply)
        self.assertNotIn('test-secret', json.dumps(payload))


if __name__ == '__main__':
    unittest.main()
