"""Offline contract tests: preflight and runtime must agree without API calls."""
import unittest
from unittest.mock import patch
import pandas as pd
from backend.blocks.catalog import CATALOG, normalize_config
from backend.blocks.sixtyfour import APIError
from backend.blocks.find_decision_makers import expand_rows
from backend.engine.compatibility import (
    CONTRACTS, DatasetSchema, company_columns, required_columns,
    validate_input, output_schema, validate_row,
)
from backend.engine.execution import RunContext, execute
from backend.engine.build_flow import create_flow
from backend.schemas import BlockDTO, ConnectionDTO, ExecuteResponse


def cfg(kind, **values):
    return normalize_config(kind, values)


class ContractTests(unittest.TestCase):
    def test_all_nodes_have_contracts_and_resolve_requirements(self):
        self.assertEqual(set(CONTRACTS), {b['type'] for b in CATALOG})
        for item in CATALOG:
            values = {f['key']: f['default'] for f in item['fields']}
            self.assertIsInstance(required_columns(item['type'], values), set)

    def test_metadata_rejected_by_every_enrichment(self):
        for item in CATALOG:
            if item['group'] == 'Enrichment':
                with self.subTest(kind=item['type']), self.assertRaisesRegex(ValueError, 'metadata'):
                    validate_input(item['type'], cfg(item['type']), DatasetSchema('metadata'))

    def test_people_tools_reject_companies(self):
        for kind in ('enrich_lead', 'find_email', 'find_phone', 'reverse_email'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'requires people'):
                validate_input(kind, cfg(kind), DatasetSchema('companies', {'email'}))

    def test_company_research_on_people_requires_mapping(self):
        schema = DatasetSchema('people', {'employer'})
        with self.assertRaises(ValueError):
            validate_input('research_companies', cfg('research_companies'), schema)
        validate_input('research_companies', cfg('research_companies', input_mapping={'name':'employer'}), schema)

    def test_configured_columns_checked(self):
        cases = [('reverse_email', {'email_column':'work_email'}),
                 ('enrich_linkedin', {'linkedin_url_column':'profile'}),
                 ('deduplicate_rows', {'key_columns':['id']}),
                 ('qualify_leads', {'reference_columns':['evidence']}),
                 ('enrich_lead', {'input_mapping':{'name':'full_name'}}),
                 ('filter', {'rules':[{'column':'score','operator':'gt','value':3}]})]
        for kind, values in cases:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Missing columns'):
                validate_input(kind, cfg(kind, **values), DatasetSchema('people'))
            validate_input(kind, cfg(kind, **values), DatasetSchema('people', set(), True))

    def test_legacy_filter_references(self):
        self.assertEqual(required_columns('filter', {'rule':'df[df["email"].notna()]'}), {'email'})

    def test_source_modes(self):
        for kind in ('find_prospects', 'search_by_filters'):
            for mode, expected in [('people','people'), ('company','companies')]:
                values = cfg(kind, query='x', mode=mode)
                self.assertEqual(output_schema(kind, values, DatasetSchema(), {}).mode, expected)
        for kind in ('get_search_fields', 'get_search_field_values'):
            self.assertEqual(output_schema(kind, cfg(kind), DatasetSchema(), {}).mode, 'metadata')

    def test_csv_mode_default_and_explicit(self):
        with patch('backend.blocks.read_csv.read_csv_header', return_value=['name']):
            for mode in ('unknown', 'people', 'companies'):
                result = output_schema('read_csv', cfg('read_csv', dataset_mode=mode), DatasetSchema(), {})
                self.assertEqual(result.mode, mode)
                self.assertIn('name', result.columns)

    def test_table_utilities_preserve_mode(self):
        for kind in ('inspect_dataset', 'save_csv', 'filter', 'deduplicate_rows'):
            schema = DatasetSchema('companies', {'email'})
            self.assertEqual(output_schema(kind, cfg(kind), schema, {}).mode, 'companies')

    def test_merge_modes_and_union(self):
        schema = DatasetSchema('people', {'name'})
        config = cfg('merge_datasets', block_ids=['a'])
        result = output_schema('merge_datasets', config, schema, {'a':DatasetSchema('people', {'email'})})
        self.assertTrue({'email', 'name'} <= result.columns)
        for mode in ('companies', 'metadata', 'unknown'):
            with self.assertRaisesRegex(ValueError, 'matching dataset modes'):
                output_schema('merge_datasets', config, schema, {'a':DatasetSchema(mode)})

    def test_discovery_transition_and_names(self):
        result = output_schema('find_decision_makers', cfg('find_decision_makers'), DatasetSchema('companies', {'company_name', 'domain'}), {})
        self.assertEqual(result.mode, 'people')
        self.assertTrue({'company_name', 'company_domain', 'name'} <= result.columns)
        row = {'company_name':'Acme', '_row_id':'a', '_raw_response':'{}'}
        actual = expand_rows(row, {'leads':[{'name':'Jane'}]})[0]
        self.assertEqual(actual['company_name'], 'Acme')
        self.assertEqual(actual['name'], 'Jane')
        self.assertNotIn('company_company_name', actual)

    def test_discovery_collisions_rejected(self):
        with self.assertRaises(ValueError):
            company_columns({'name', 'company_name'})
        with self.assertRaisesRegex(ValueError, 'collide'):
            validate_input('find_decision_makers', cfg('find_decision_makers', lead_struct={'company_name':'Person name'}), DatasetSchema('companies', {'name'}))
        with self.assertRaisesRegex(APIError, 'collide'):
            expand_rows({'name':'Acme','_row_id':'a','_raw_response':'{}'}, {'leads':[{'company_name':'Jane'}]})

    def test_reverse_requires_single_email(self):
        config = cfg('reverse_email')
        for value in (None, '', 'a@b.test, c@d.test', ['a@b.test'], 'invalid'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_row('reverse_email', {'email':value}, {}, config)
        validate_row('reverse_email', {'email':'a@b.test'}, {}, config)

    def test_linkedin_mode_and_references(self):
        with self.assertRaisesRegex(ValueError, 'dataset mode'):
            validate_row('enrich_linkedin', {'linkedin':'https://linkedin.com/company/acme'}, {}, cfg('enrich_linkedin'), 'people')
        with self.assertRaisesRegex(ValueError, 'reference URL'):
            validate_row('qualify_leads', {'url':'not a URL'}, {}, cfg('qualify_leads', reference_columns=['url']))

    def test_failed_discovery_row_cannot_reenter_enrichment(self):
        for kind in ('enrich_lead', 'find_email', 'qualify_leads'):
            with self.assertRaisesRegex(ValueError, 'no person'):
                validate_row(kind, {'_entity_mode':'companies'}, {}, cfg(kind), 'people')

    def test_preflight_rejects_before_execution(self):
        blocks = [BlockDTO(id='a', type='get_search_fields'), BlockDTO(id='b', type='enrich_lead')]
        result = create_flow(blocks, [ConnectionDTO(fromId='a', toId='b')])
        self.assertIsInstance(result, ExecuteResponse)
        self.assertIn('b (enrich_lead)', result.error)

    def test_runtime_missing_provider_field_stops_before_call(self):
        blocks = [BlockDTO(id='a', type='find_prospects', config=cfg('find_prospects', query='x')),
                  BlockDTO(id='b', type='reverse_email', config=cfg('reverse_email'))]
        events = []; context = RunContext(events.append)
        with patch('backend.engine.execution.get_block_function', return_value=lambda *args: pd.DataFrame({'name':['Jane']})) as resolve:
            execute(blocks, context)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(events[-1]['event'], 'error')
        self.assertIn('email', events[-1]['message'])
        self.assertEqual(context.mode, 'people')
        self.assertEqual(context.columns, {'name', '_row_id'})

    def test_bad_row_values_make_no_requests(self):
        from backend.blocks.reverse_email import reverse_email
        frame = pd.DataFrame({'email':['a@b.test, c@d.test'], '_row_id':['a']})
        with patch('backend.blocks.row_processing.SixtyfourClient') as client:
            result = reverse_email(frame, cfg('reverse_email'), RunContext(lambda e: None))
        client.return_value.job.assert_not_called()
        self.assertEqual(result.attrs['failed_rows'], 1)

    def test_full_company_to_people_flow(self):
        from backend.engine.registry import get_block_function
        blocks = [BlockDTO(id=str(i), type=kind, config=cfg(kind, **values)) for i, (kind, values) in enumerate([
            ('search_by_filters', {'mode':'company'}), ('find_decision_makers', {}),
            ('find_email', {}), ('reverse_email', {}),
        ])]
        def resolve(kind):
            if kind == 'search_by_filters':
                return lambda *args: pd.DataFrame({'company_name':['Acme']})
            return get_block_function(kind)
        events = []; context = RunContext(events.append)
        with patch('backend.engine.execution.get_block_function', side_effect=resolve), patch('backend.blocks.row_processing.SixtyfourClient') as client:
            client.return_value.job.side_effect = [
                {'leads':[{'name':'Jane'}]},
                {'email':[['jane@acme.test','OK','PROFESSIONAL']]},
                {'title':'CTO'},
            ]
            execute(blocks, context)
        self.assertEqual(events[-1]['event'], 'complete')
        self.assertEqual(context.mode, 'people')
        self.assertEqual(client.return_value.job.call_count, 3)
        self.assertEqual(context.snapshots['3'].iloc[0]['company_name'], 'Acme')

    def test_failed_expansion_no_downstream_requests_parallel(self):
        from backend.engine.registry import get_block_function
        blocks = [BlockDTO(id=str(i), type=kind, config=cfg(kind, **values)) for i, (kind, values) in enumerate([
            ('search_by_filters', {'mode':'company'}), ('find_decision_makers', {}), ('find_email', {}),
        ])]
        def resolve(kind):
            if kind == 'search_by_filters':
                return lambda *args: pd.DataFrame({'name':['Acme','Other']})
            return get_block_function(kind)
        events = []
        with patch('backend.engine.execution.get_block_function', side_effect=resolve), patch('backend.blocks.row_processing.SixtyfourClient') as client:
            client.return_value.job.return_value = {'unexpected': 'shape'}
            execute(blocks, RunContext(events.append))
        self.assertEqual(client.return_value.job.call_count, 2)
        self.assertEqual(events[-1]['status'], 'partial')

    def test_strict_row_failure_stops_without_request(self):
        from backend.blocks.reverse_email import reverse_email
        with patch('backend.blocks.row_processing.SixtyfourClient') as client:
            with self.assertRaises(ValueError):
                reverse_email(pd.DataFrame({'email':['invalid']}), cfg('reverse_email', continue_on_error=False), RunContext(lambda e: None))
        client.return_value.job.assert_not_called()
