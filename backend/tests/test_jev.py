"""Offline Jev routing regressions: real local tools, mocked model transports."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
import pandas as pd
from fastapi.testclient import TestClient
from backend.agent.graph import AgentRun, execute_run
from backend.agent.jev import candidates, JevRouter, Selection, DEFER
from backend.agent.limits import Limits
from backend.agent.model import ModelError
from backend.agent.schemas import RunRequest
from backend.engine.compatibility import DatasetSchema
from backend.tests.test_agent import ScriptedPlanner, contract, decision, plan, tool


def seeded_run(script, use_jev=True, rows=None, **kwargs):
    run = AgentRun('Keep two unique people with emails.', use_jev=use_jev,
                   planner=ScriptedPlanner(script), **kwargs)
    df = pd.DataFrame(rows if rows is not None else [
        {'name': 'Ada', 'email': 'ada@example.com'},
        {'name': 'Ada', 'email': 'ada@example.com'},
        {'name': 'Grace', 'email': 'grace@example.com'},
        {'name': 'Missing', 'email': None},
    ])
    run.context.snapshots['seed'] = df
    run.context.schema_snapshots['seed'] = DatasetSchema('people', set(df.columns))
    run.current = 'seed'
    run.jev = Mock()
    run.jev.choose = AsyncMock()
    return run


class JevLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.export = patch('backend.engine.execution.ROOT', Path(self.temp.name))
        self.export.start()
        self.addCleanup(self.export.stop)

    async def test_jev_selects_two_real_tools_and_skips_planner(self):
        run = seeded_run([plan(min_rows=2, max_rows=2, unique_by=['email'])])
        run.jev.choose.side_effect = [Selection('filter', .95), Selection('deduplicate_rows', .99)]
        await execute_run(run)
        self.assertEqual(run.status, 'completed', run.final)
        self.assertEqual(len(run.data()), 2)
        self.assertEqual(len(run.planner.observations), 1)
        self.assertEqual(run.budget()['planner_requests'], 1)
        self.assertEqual(run.jev_requests, 2)
        self.assertEqual(run.jev_selections, 2)
        self.assertEqual(run.model_requests, 3)
        self.assertTrue(run.final['downloads'])

    async def test_default_path_never_calls_jev(self):
        run = seeded_run([plan(min_rows=2, unique_by=['email']),
                          tool('filter', {'rules': [{'column': 'email', 'operator': 'is_present'}]}),
                          tool('deduplicate_rows', {'key_columns': ['email']})], use_jev=False)
        await execute_run(run)
        self.assertEqual(run.status, 'completed')
        run.jev.choose.assert_not_called()
        self.assertNotIn('jev_requests', run.budget())
        self.assertFalse(RunRequest(query='test').use_jev)

    async def test_low_confidence_defer_and_bad_choice_fall_back(self):
        for selection in (Selection('filter', .5), Selection(DEFER, .99), Selection('finish', .99)):
            with self.subTest(selection=selection):
                run = seeded_run([plan(min_rows=2), decision('partial', message='Need more planning.')])
                run.jev.choose.return_value = selection
                await execute_run(run)
                self.assertEqual(run.status, 'partial')
                self.assertEqual(run.tool_attempts, 0)
                self.assertEqual(run.jev_fallbacks, 1)
                self.assertEqual(run.model_requests, 3)

    async def test_clear_choice_below_old_cutoff_executes(self):
        run = seeded_run([plan(min_rows=2, max_rows=2, unique_by=['email'])])
        run.jev.choose.side_effect = [
            Selection('filter', .55, probability=.72, runner_up_probability=.2),
            Selection('deduplicate_rows', .7, probability=.75, runner_up_probability=.25)]
        await execute_run(run)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.jev_selections, 2)
        self.assertEqual(len(run.planner.observations), 1)

    async def test_provider_confidence_does_not_override_ambiguous_choice(self):
        run = seeded_run([plan(min_rows=2), decision('partial')])
        run.jev.choose.return_value = Selection('filter', .99, probability=.51, runner_up_probability=.49)
        await execute_run(run)
        self.assertEqual(run.jev_selections, 0)
        self.assertEqual(run.jev_fallbacks, 1)

    async def test_configured_cutoff_is_enforced(self):
        run = seeded_run([plan(min_rows=2), decision('partial')])
        run.jev.choose.return_value = Selection('filter', .95, probability=.75, runner_up_probability=.2)
        with patch.dict(os.environ, {'JEV_MIN_PROBABILITY': '0.8'}):
            await execute_run(run)
        self.assertEqual(run.jev_fallbacks, 1)

    async def test_transport_failure_disables_jev_for_run(self):
        for error in (ModelError('HTTP 401'), TimeoutError()):
            run = seeded_run([plan(min_rows=2, unique_by=['email']),
                              tool('filter', {'rules': [{'column': 'email', 'operator': 'is_present'}]}),
                              tool('deduplicate_rows', {'key_columns': ['email']})])
            run.jev.choose.side_effect = error
            await execute_run(run)
            self.assertEqual(run.status, 'completed')
            self.assertEqual(run.jev_requests, 1)
            self.assertTrue(run.jev_disabled)

    async def test_last_request_reserved_for_planner(self):
        run = seeded_run([plan(min_rows=2), decision('partial')], limits=Limits(model_requests=2))
        await execute_run(run)
        run.jev.choose.assert_not_called()
        self.assertEqual(run.model_requests, 2)

    async def test_candidate_preview_failure_uses_planner_without_a_jev_call(self):
        run = seeded_run([plan(min_rows=2), decision('partial')])
        with patch('backend.agent.graph.candidates', side_effect=TypeError('Unexpected provider value')):
            await execute_run(run)
        self.assertEqual(run.status, 'partial')
        run.jev.choose.assert_not_called()

    async def test_completion_needs_no_jev_or_extra_planner(self):
        run = seeded_run([plan(min_rows=1)], rows=[{'email': 'ada@example.com'}])
        await execute_run(run)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.model_requests, 1)
        run.jev.choose.assert_not_called()

    async def test_cancellation_during_jev_stops_without_fallback(self):
        run = seeded_run([plan(min_rows=2)])
        entered = asyncio.Event()
        async def wait(*_):
            entered.set()
            await asyncio.sleep(100)
        run.jev.choose.side_effect = wait
        task = asyncio.create_task(execute_run(run))
        await asyncio.wait_for(entered.wait(), 2)
        run.context.cancelled.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(run.status, 'cancelled')
        self.assertEqual(run.tool_attempts, 0)
        self.assertEqual(len(run.planner.observations), 1)


class CandidateTests(unittest.TestCase):
    def test_personal_email_phone_and_condition_arguments(self):
        run = seeded_run([], rows=[{'name': 'Ada', 'score': 1}, {'name': 'Grace', 'score': 3}])
        run.contract = contract(min_rows=1, required_columns=['personal_email', 'phone'], conditions=[
            {'column': 'score', 'operator': 'gte', 'value_json': '2'}])
        options = candidates(run)
        personal = json.loads(options['find_personal_email'].arguments_json)
        self.assertEqual(personal['mode'], 'PERSONAL')
        self.assertTrue(personal['verify_emails'])
        self.assertIn('find_phone', options)
        self.assertEqual(json.loads(options['filter'].arguments_json)['rules'], [
            {'column': 'score', 'operator': 'gte', 'value': 2}])

    def test_contract_drives_arguments_and_noop_duplicates_are_excluded(self):
        run = seeded_run([])
        run.contract = contract(min_rows=2, unique_by=['email'])
        options = candidates(run)
        self.assertIn('filter', options)
        self.assertIn('deduplicate_rows', options)
        self.assertIn('find_email', options)
        from backend.agent.tools import prepare
        run.attempted.add(prepare(run, options['filter'])[-1])
        self.assertNotIn('filter', candidates(run))
        self.assertEqual(json.loads(options['find_email'].arguments_json)['only_missing'], True)

    def test_no_guessed_mappings_or_destructive_shortfall(self):
        run = seeded_run([], rows=[{'custom_name': 'Ada', 'email': None}, {'email': 'a@example.com'}])
        run.contract = contract(min_rows=2)
        self.assertEqual(candidates(run), {})
        run.context.schema_snapshots['seed'].mode = 'unknown'
        self.assertEqual(candidates(run), {})

    def test_recovery_and_unresolved_requirements_use_planner(self):
        run = seeded_run([])
        run.contract = contract(min_rows=2, unresolved_requirements=['Unknown requirement'])
        self.assertEqual(candidates(run), {})
        run.contract = contract(min_rows=2)
        run.tool_failures = 1
        self.assertEqual(candidates(run), {})


class JevTransportTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, body, status=200):
        response = Mock(status_code=status)
        response.json.return_value = body
        client = AsyncMock()
        client.post.return_value = response
        run = seeded_run([])
        run.contract = contract(min_rows=2, unique_by=['email'])
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'direct-secret'}), \
             patch('backend.agent.jev.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            result = await JevRouter().choose({'dataset': {'row_count': 4}}, candidates(run), 5)
        return result, client

    async def test_typesafe_key_uses_direct_endpoint_and_snake_case_usage(self):
        result, client = await self.request({'model': 'jev-latest', 'answers': {'next_action': {
            'type': 'choice', 'choice': 'filter', 'confidence': .65,
            'probabilities': {'filter': .75, DEFER: .15, 'deduplicate_rows': .05, 'find_email': .05}}},
            'usage': {'input_tokens': 120, 'output_tokens': 12}})
        self.assertEqual(result, Selection('filter', .65, 120, 12, None, .75, .15))
        args = client.post.call_args
        self.assertEqual(args.args[0], 'https://api.typesafe.ai/v1/systemone')
        self.assertEqual(args.kwargs['headers']['Authorization'], 'Bearer direct-secret')
        self.assertEqual(args.kwargs['json']['model'], 'jev-latest')
        self.assertNotIn('direct-secret', json.dumps(args.kwargs['json']))

    async def test_invalid_scores_rejected_even_with_valid_choice(self):
        for confidence, probabilities in (
            (float('nan'), {'filter': .8, DEFER: .2, 'deduplicate_rows': 0, 'find_email': 0}),
            (.9, {'filter': .4, DEFER: .6, 'deduplicate_rows': 0, 'find_email': 0}),
            (.9, {'filter': .9, DEFER: .9, 'deduplicate_rows': 0, 'find_email': 0}),
            (.9, {'filter': True, DEFER: 0, 'deduplicate_rows': 0, 'find_email': 0}),
        ):
            with self.subTest(probabilities=probabilities), self.assertRaises(ModelError):
                await self.request({'answers': {'next_action': {'type': 'choice', 'choice': 'filter',
                    'confidence': confidence, 'probabilities': probabilities}}})

    async def test_direct_key_is_redacted(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'direct-secret'}):
            self.assertEqual(AgentRun('test').redact('Error direct-secret'), 'Error [redacted]')

    async def test_malformed_or_untrusted_responses_cannot_select_tools(self):
        for answer in ({}, {'type': 'choice', 'choice': 'finish', 'probabilities': {'finish': 1}},
                       {'type': 'choice', 'choice': 'filter', 'probabilities': {'filter': float('nan')}},
                       {'type': 'choice', 'choice': 'filter', 'probabilities': {'filter': True}}):
            with self.subTest(answer=answer), self.assertRaises(ModelError):
                await self.request({'answers': {'next_action': answer}})
        with self.assertRaisesRegex(ModelError, 'HTTP 401'):
            await self.request({'error': 'test-secret'}, 401)


class JevAPITests(unittest.TestCase):
    def test_resume_preserves_choice_and_rejects_switching(self):
        from backend.main import app
        from backend.agent.routes import RUNS
        RUNS.clear()
        client = TestClient(app)
        planner = ScriptedPlanner([decision('clarify', message='Which industry?'), decision('partial')])
        with patch('backend.agent.routes.configuration', return_value={'configured': True, 'model': 'test'}), \
             patch('backend.agent.graph.Planner.decide', side_effect=planner.decide):
            response = client.post('/agent/runs', json={'query': 'Find leads', 'use_jev': True})
            run_id = json.loads(response.text.splitlines()[0])['run_id']
            self.assertTrue(RUNS[run_id].use_jev)
            self.assertEqual(client.post('/agent/runs', json={
                'query': 'Software', 'run_id': run_id, 'use_jev': False}).status_code, 409)
            response = client.post('/agent/runs', json={'query': 'Software', 'run_id': run_id})
            self.assertTrue(json.loads(response.text.splitlines()[-1])['use_jev'])
            self.assertEqual(RUNS[run_id].model_requests, 2)


if __name__ == '__main__':
    unittest.main()
