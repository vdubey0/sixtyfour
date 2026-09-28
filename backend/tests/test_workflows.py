"""Offline regression tests: no real credentials or paid API requests are used."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import pandas as pd
import requests
from backend.blocks.catalog import CATALOG, normalize_config
from backend.blocks.deduplicate_rows import deduplicate_rows
from backend.blocks.merge_datasets import merge_datasets
from backend.blocks.qualify_leads import parse_response as parse_qualification
from backend.blocks.filter import filter, legacy_expression
from backend.blocks.read_csv import read_csv, resolve_csv
from backend.blocks.sixtyfour import APIError, SixtyfourClient
from backend.engine.build_flow import create_flow
from backend.engine.execution import RunContext, execute, Cancelled
from backend.engine.registry import get_block_function
from backend.schemas import BlockDTO, ConnectionDTO, ExecuteResponse


def run_named_block(df, config, context, kind):
    return get_block_function(kind)(df, config, context)


def config(kind, **kwargs):
    return normalize_config(kind, kwargs)


def context():
    ctx = RunContext(lambda event: None)
    ctx.block_id = 'test'
    return ctx


def graph(types, configs=None):
    configs = configs or {}
    blocks = [BlockDTO(id=str(i), type=kind, config=configs.get(str(i), {})) for i, kind in enumerate(types)]
    edges = [ConnectionDTO(fromId=str(i), toId=str(i+1)) for i in range(len(types)-1)]
    return blocks, edges


class GraphTests(unittest.TestCase):
    def test_all_blocks_registered(self):
        self.assertEqual(len(CATALOG), 18)
        for item in CATALOG:
            self.assertTrue(callable(get_block_function(item['type'])))

    def test_empty_graph_is_readable_error(self):
        self.assertIsInstance(create_flow([], []), ExecuteResponse)

    def test_duplicate_ids(self):
        blocks, edges = graph(['read_csv', 'save_csv'])
        blocks[1].id = '0'
        self.assertIsInstance(create_flow(blocks, edges), ExecuteResponse)

    def test_dangling_edges(self):
        blocks, edges = graph(['read_csv'])
        edges.append(ConnectionDTO(fromId='0', toId='missing'))
        self.assertIsInstance(create_flow(blocks, edges), ExecuteResponse)

    def test_branches_rejected(self):
        blocks, edges = graph(['read_csv', 'inspect_dataset', 'save_csv'])
        edges.append(ConnectionDTO(fromId='0', toId='2'))
        self.assertIsInstance(create_flow(blocks, edges), ExecuteResponse)

    def test_disconnected_cycle_rejected(self):
        blocks, edges = graph(['read_csv', 'filter', 'save_csv'])
        edges = [ConnectionDTO(fromId='1', toId='2'), ConnectionDTO(fromId='2', toId='1')]
        self.assertIsInstance(create_flow(blocks, edges), ExecuteResponse)

    def test_search_source_and_preview_only(self):
        blocks, edges = graph(['find_prospects', 'inspect_dataset'], {'0': {'query': 'SaaS companies'}})
        self.assertIsInstance(create_flow(blocks, edges), list)

    def test_sources_cannot_replace_existing_table(self):
        self.assertIsInstance(create_flow(*graph(['read_csv', 'search_by_filters'])), ExecuteResponse)

    def test_old_enrichment_fields_migrate(self):
        actual = config('enrich_lead', fields=['title', 'github'])
        self.assertEqual(set(actual['struct']), {'title', 'github_url'})

    def test_bad_config_before_execution(self):
        for kind, values in [('find_email', {'mode': 'INVALID'}), ('find_prospects', {'query': 'x', 'max_results': 0}), ('qualify_leads', {'qualification_criteria': [{'criteria_name':'fit','description':'fit','threshold':8}]}), ('enrich_lead', {'struct': {}})]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                config(kind, **values)

    def test_merge_must_reference_earlier_block(self):
        self.assertIsInstance(create_flow(*graph(['read_csv', 'merge_datasets'], {'1': {'block_ids': ['future']}})), ExecuteResponse)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({'email': ['a@example.test', '', None, 'a@example.test'], 'count': ['10', '2', 'bad', '30'], '_row_id': ['a','b','c','d']})

    def test_structured_numeric_and_missing(self):
        result = filter(self.df, config('filter', rules=[{'column':'count','operator':'gte','value':10}]))
        self.assertEqual(result['_row_id'].tolist(), ['a','d'])
        result = filter(self.df, config('filter', rules=[{'column':'email','operator':'is_missing'}]))
        self.assertEqual(result['_row_id'].tolist(), ['b','c'])

    def test_legacy_expression_preserved(self):
        result = legacy_expression(self.df, 'df[df["email"].isna() | (df["email"].str.strip() == "")]')
        self.assertEqual(result['_row_id'].tolist(), ['b','c'])

    def test_arbitrary_python_rejected(self):
        for expression in ['__import__("os").getcwd()', 'df.to_csv("bad.csv")', 'df.__class__', '[x for x in df]']:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                legacy_expression(self.df, expression)

    def test_dedup_preserves_missing_keys(self):
        result = deduplicate_rows(self.df, config('deduplicate_rows'), context())
        self.assertEqual(result['_row_id'].tolist(), ['a','b','c'])

    def test_merge_keeps_lineage_unique_ids(self):
        ctx = context(); ctx.snapshots['a'] = self.df.iloc[:1]
        result = merge_datasets(self.df, config('merge_datasets', block_ids=['a']), ctx)
        self.assertEqual(len(result), 5)
        self.assertTrue(result['_row_id'].is_unique)
        self.assertIn('_parent_row_id', result)

    def test_csv_rejects_external_files(self):
        with self.assertRaises(ValueError): resolve_csv('/etc/passwd')

    def test_csv_preserves_leading_zeroes_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'inputs').mkdir()
            path=root/'inputs'/'test.csv'; path.write_text('id,phone\n001,+15551234567\n')
            with patch('backend.blocks.read_csv.ROOT', root):
                result=read_csv('inputs/test.csv')
                self.assertEqual(result.iloc[0]['id'], '001')
                self.assertEqual(result.iloc[0]['phone'], '+15551234567')
                path.write_text('id,id\n1,2\n')
                with self.assertRaises(ValueError): read_csv('inputs/test.csv')

    def test_local_flow_preview_download_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            events=[]; ctx=RunContext(events.append)
            blocks, edges=graph(['read_csv','filter','save_csv'], {'1': {'rules':[{'column':'email','operator':'is_present'}]}})
            with patch('backend.blocks.read_csv.read_csv', return_value=self.df), patch('backend.engine.execution.ROOT', Path(temp)):
                execute(create_flow(blocks,edges),ctx)
            self.assertEqual(events[-1]['event'], 'complete')
            self.assertEqual(events[-1]['row_count'], 2)
            paths=list(Path(temp).rglob('*.csv'))
            self.assertEqual(len(paths),1)
            self.assertNotIn('Unnamed: 0',pd.read_csv(paths[0]).columns)
            self.assertEqual(ctx.snapshots['0'].shape[0],4)
            self.assertNotEqual(ctx.run_id,context().run_id)


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame([{'_row_id':'a','name':'Example Person','company':'Example','email':None}])

    def run_block(self, kind, response, **kwargs):
        with patch('backend.blocks.row_processing.SixtyfourClient') as cls:
            client=cls.return_value; client.job.return_value=response; client.request.return_value=response
            client.job_timeout=900
            result=run_named_block(self.df,config(kind,**kwargs),context(),kind)
            return result,client

    def test_people_current_endpoint_and_mapping(self):
        result,client=self.run_block('enrich_lead', {'structured_data':{'title':'CTO'},'references':{'https://example.test':'source'}},input_mapping={'name':'name'})
        self.assertEqual(client.job.call_args[0][0],'/people-intelligence-async')
        self.assertEqual(client.job.call_args[0][1]['lead_info'],{'name':'Example Person'})
        self.assertEqual(result.iloc[0]['title'],'CTO')
        self.assertIn('example.test',result.iloc[0]['_references'])

    def test_existing_values_not_duplicated_or_overwritten(self):
        result,_=self.run_block('enrich_lead',{'structured_data':{'company':'Other'}})
        self.assertEqual(result.iloc[0]['company'],'Example')
        self.assertTrue(result.columns.is_unique)
        result,_=self.run_block('enrich_lead',{'structured_data':{'company':'Other'}},overwrite=True)
        self.assertEqual(result.iloc[0]['company'],'Other')

    def test_email_status_and_modern_arguments(self):
        result,client=self.run_block('find_email',{'email':[['a@example.test','UNKNOWN','COMPANY']]})
        self.assertEqual(result.iloc[0]['email_status'],'UNKNOWN')
        payload=client.job.call_args[0][1]
        self.assertEqual(payload['mode'],'PROFESSIONAL')
        self.assertTrue(payload['verify_emails'])
        self.assertNotIn('bruteforce',payload)
        self.assertNotIn('only_company_emails',payload)

    def test_personal_email_response(self):
        result,_=self.run_block('find_email',{'email':[], 'personal_email':[['p@example.test','OK','PERSONAL']]},mode='PERSONAL')
        self.assertEqual(result.iloc[0]['personal_email'],'p@example.test')

    def test_skip_existing_email(self):
        self.df['email']='existing@example.test'
        result,client=self.run_block('find_email',{})
        client.job.assert_not_called()
        self.assertEqual(result.iloc[0]['_status'],'skipped')

    def test_phone_and_reverse_preserve_provider_shape(self):
        result,_=self.run_block('find_phone',{'phone':'+15551234567','provider':'example'})
        self.assertEqual(result.iloc[0]['phone'],'+15551234567')
        self.df['email']='inbound@example.test'
        result,client=self.run_block('reverse_email',{'name':'Resolved Name'},overwrite=True)
        self.assertEqual(client.job.call_args[0][1],{'lead':{'email':'inbound@example.test'}})
        self.assertEqual(result.iloc[0]['name'],'Resolved Name')

    def test_linkedin_request_and_url_validation(self):
        self.df['linkedin']='https://www.linkedin.com/in/example'
        result,client=self.run_block('enrich_linkedin',{'structured_data':{'title':'Engineer'}})
        self.assertEqual(client.request.call_args[0][0:2],('POST','/enrich-linkedin'))
        self.assertEqual(result.iloc[0]['title'],'Engineer')
        self.df['linkedin']='https://linkedin.com.attacker.test/in/person'
        result,client=self.run_block('enrich_linkedin',{})
        client.request.assert_not_called()
        self.assertEqual(result.attrs['failed_rows'],1)

    def test_company_discovery_expands_and_preserves_parent(self):
        result,client=self.run_block('find_decision_makers',{'structured_data':{'company_name':'Example'},'leads':[{'name':'A'},{'name':'B'}]})
        self.assertEqual(len(result),2)
        self.assertEqual(result['_parent_row_id'].tolist(),['a','a'])
        self.assertTrue(result['_row_id'].is_unique)
        self.assertTrue(client.job.call_args[0][1]['find_people'])

    def test_empty_discovery_is_not_failure(self):
        result,_=self.run_block('find_decision_makers',{'leads':[]})
        self.assertEqual(len(result),0)
        self.assertIn('name',result.columns)

    def test_unknown_discovery_shape_is_visible_failure(self):
        result,_=self.run_block('find_decision_makers',{'unexpected':'shape'})
        self.assertEqual(result.attrs['failed_rows'],1)
        self.assertIn('unexpected',result.iloc[0]['_raw_response'])

    def test_qa_numeric_parsing(self):
        result,_=self.run_block('qualify_leads',{'structured_data':{'qualification_score':'8.2','qualification_verdict':'accept'}})
        self.assertEqual(result.iloc[0]['qualification_score'],8.2)
        self.assertIsNone(parse_qualification({'structured_data':{'qualification_score':'not enough evidence'}})['qualification_score'])

    def test_auth_stops_instead_of_calling_every_row(self):
        with patch('backend.blocks.row_processing.SixtyfourClient') as cls:
            cls.return_value.job.side_effect=APIError('Invalid key',401)
            with self.assertRaises(APIError): run_named_block(self.df,config('enrich_lead'),context(),'enrich_lead')

    def test_null_api_response_is_failure_not_skip(self):
        result, client = self.run_block('enrich_lead', None)
        client.job.assert_called_once()
        self.assertEqual(result.iloc[0]['_status'], 'failed')
        self.assertEqual(result.attrs['failed_rows'], 1)

    def test_row_failure_is_partial(self):
        with patch('backend.blocks.row_processing.SixtyfourClient') as cls:
            cls.return_value.job.side_effect=APIError('No result',500)
            result=run_named_block(self.df,config('enrich_lead'),context(),'enrich_lead')
            self.assertEqual(result.attrs['failed_rows'],1)
            self.assertEqual(result.iloc[0]['_error'],'No result')


class SearchTests(unittest.TestCase):
    def test_empty_page_with_more_is_followed_using_cursor_only(self):
        with patch('backend.blocks.search_by_filters.SixtyfourClient') as cls:
            client=cls.return_value
            client.request.side_effect=[{'results':[], 'has_more':True,'next_cursor':'next'}, {'results':[{'name':'A'},{'name':'B'}],'has_more':False}]
            result=run_named_block(None,config('search_by_filters',max_results=1),context(),'search_by_filters')
            self.assertEqual(len(result),1)
            self.assertEqual(client.request.call_args[0][2],{'cursor':'next'})

    def test_repeated_cursor_rejected(self):
        with patch('backend.blocks.search_by_filters.SixtyfourClient') as cls:
            cls.return_value.request.return_value={'results':[], 'has_more':True,'next_cursor':'same'}
            with self.assertRaises(APIError): run_named_block(None,config('search_by_filters'),context(),'search_by_filters')

    def test_deep_search_job_then_query(self):
        with patch('backend.blocks.find_prospects.SixtyfourClient') as cls:
            client=cls.return_value; client.job.return_value={'search_id':'search-1'}
            client.request.return_value={'results':[{'name':'A'}],'has_more':False}
            result=run_named_block(None,config('find_prospects',query='CTOs'),context(),'find_prospects')
            self.assertEqual(len(result),1)
            self.assertEqual(client.job.call_args[0][1]['output_mode'],'query_only')
            self.assertEqual(client.request.call_args[0][2]['search_id'],'search-1')

    def test_discovery_metadata_blocks(self):
        for kind, key in [('get_search_fields','fields'),('get_search_field_values','values')]:
            with self.subTest(kind=kind),patch('backend.blocks.' + kind + '.SixtyfourClient') as cls:
                cls.return_value.request.return_value={key:[{'field':'industry'}],'limits':{}}
                ctx=context(); result=run_named_block(None,config(kind),ctx,kind)
                self.assertEqual(len(result),1)
                self.assertIn('limits',ctx.metadata)


class TransportTests(unittest.TestCase):
    def client(self):
        ctx=context(); ctx.wait=Mock()
        with patch('backend.blocks.sixtyfour.setting',side_effect=lambda name, default=None: 'test-key' if name=='SIXTYFOUR_API_KEY' else default):
            return SixtyfourClient(ctx)

    def test_async_success_and_all_terminal_failures(self):
        client=self.client()
        with patch.object(client,'request',side_effect=[{'task_id':'j'},{'status':'completed','result':{'structured_data':{}}}]):
            self.assertEqual(client.job('/people-intelligence-async',{}),{'structured_data':{}})
        for status in ('failed','cancelled','terminated','timed_out','continued_as_new'):
            with self.subTest(status=status),patch.object(client,'request',side_effect=[{'task_id':'j'},{'status':status}]),self.assertRaises(APIError):
                client.job('/people-intelligence-async',{})

    def test_post_connection_failure_not_retried(self):
        client=self.client()
        with patch('backend.blocks.sixtyfour.requests.request',side_effect=requests.Timeout()) as req:
            with self.assertRaises(APIError): client.request('POST','/people-intelligence-async',{})
            self.assertEqual(req.call_count,1)

    def test_429_is_bounded_retry(self):
        client=self.client()
        response=Mock(status_code=429,ok=False,reason='Too many requests')
        response.json.return_value={'detail':'Rate limit'}
        with patch('backend.blocks.sixtyfour.requests.request',return_value=response) as req:
            with self.assertRaises(APIError): client.request('POST','/find-email-async',{})
            self.assertEqual(req.call_count,3)

    def test_cancel_prevents_submission(self):
        client=self.client(); client.context.cancelled.set()
        with patch('backend.blocks.sixtyfour.requests.request') as req:
            with self.assertRaises(Cancelled): client.request('POST','/find-email-async',{})
            req.assert_not_called()

    def test_poll_deadline(self):
        client=self.client(); client.job_timeout=1
        with patch.object(client,'request',return_value={'task_id':'j'}),patch('backend.blocks.sixtyfour.time.monotonic',side_effect=[0,2]):
            with self.assertRaisesRegex(APIError,'Timed out'): client.job('/people-intelligence-async',{})


class AdditionalRegressionTests(unittest.TestCase):
    def test_enrichment_preserves_order_with_concurrent_workers(self):
        df=pd.DataFrame([{'_row_id':str(i),'name':'Person '+str(i)} for i in range(12)])
        with patch('backend.blocks.row_processing.SixtyfourClient') as cls:
            cls.return_value.job.side_effect=lambda endpoint,payload: {'structured_data':{'title':payload['lead_info']['name']}}
            result=run_named_block(df,config('enrich_lead',max_workers=4),context(),'enrich_lead')
        self.assertEqual(result['_row_id'].tolist(),[str(i) for i in range(12)])
        self.assertEqual(result['title'].tolist(),df['name'].tolist())

    def test_history_survives_later_api_blocks(self):
        df=pd.DataFrame([{'_row_id':'a','name':'Example'}])
        with patch('backend.blocks.row_processing.SixtyfourClient') as cls:
            cls.return_value.job.return_value={'structured_data':{'title':'CTO'}}
            first=run_named_block(df,config('enrich_lead'),context(),'enrich_lead')
            cls.return_value.job.return_value={'email':[['a@example.test','OK','COMPANY']]}
            second=run_named_block(first,config('find_email'),context(),'find_email')
        history=json.loads(second.iloc[0]['_history'])
        self.assertEqual([h['tool'] for h in history],['enrich_lead','find_email'])
        self.assertIn('CTO',history[0]['response'])

    def test_payload_keys_match_current_public_schema(self):
        snapshot=json.loads((Path(__file__).parent/'api_contract_snapshot.json').read_text())['requests']
        df=pd.DataFrame([{'_row_id':'a','name':'Example','email':'a@example.test','linkedin':'https://www.linkedin.com/in/example'}])
        responses={
            'enrich_lead':{'structured_data':{}}, 'research_companies':{'structured_data':{}},
            'find_decision_makers':{'leads':[]}, 'find_email':{'email':[]},
            'find_phone':{'phone':[]}, 'reverse_email':{'name':'Example'},
            'enrich_linkedin':{'structured_data':{}}, 'qualify_leads':{'structured_data':{}}}
        for kind,response in responses.items():
            with self.subTest(kind=kind),patch('backend.blocks.row_processing.SixtyfourClient') as cls:
                client=cls.return_value; client.job.return_value=response; client.request.return_value=response;client.job_timeout=900
                run_named_block(df,config(kind,only_missing=False),context(),kind)
                if kind=='enrich_linkedin':
                    _,endpoint,payload=client.request.call_args[0]
                else: endpoint,payload=client.job.call_args[0]
                schema=snapshot[endpoint]
                self.assertTrue(set(schema['required']).issubset(payload))
                self.assertTrue(set(payload).issubset(schema['properties']))

    def test_nonfinite_values_are_json_safe(self):
        from backend.blocks.sixtyfour import clean_value
        self.assertEqual(clean_value({'a':float('inf'),'b':float('nan')}),{'a':None,'b':None})


class StreamingTests(unittest.TestCase):
    def test_closing_stream_signals_worker_cancellation(self):
        import asyncio
        from backend.engine.execution import process_flow
        contexts=[]
        stopped=threading.Event()

        def waiting_worker(order, ctx):
            contexts.append(ctx)
            ctx.emit({'event':'started'})
            try:
                ctx.wait(60)
            finally:
                stopped.set()

        async def consume():
            stream=process_flow([])
            first=await stream.__anext__()
            self.assertEqual(json.loads(first)['event'],'started')
            await stream.aclose()
            self.assertTrue(contexts[0].cancelled.is_set())

        with patch('backend.engine.execution.execute',side_effect=waiting_worker):
            asyncio.run(consume())
            self.assertTrue(stopped.wait(1))


if __name__ == '__main__':
    unittest.main()
