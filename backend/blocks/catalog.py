"""Shared block definitions served to the editor and used for validation.

Endpoints verified against https://api.sixtyfour.ai/openapi.json on 2026-09-26.
Keep execution in blocks/ and orchestration in engine/.
"""
from copy import deepcopy

DOCS = 'https://docs.sixtyfour.ai/'


def field(key, label, kind='text', default='', required=False, help='', **extra):
    # A field definition drives its editor control and backend type/default checks.
    return dict(key=key, label=label, kind=kind, default=default,
                required=required, help=help, **extra)


# Reusable field definitions keep options consistent across related tools.
# block() deep-copies them so definitions cannot accidentally share mutable defaults.
MAPPING = field('input_mapping', 'Column mapping', 'json', {}, help='Optional API field → CSV column, e.g. {"name":"full_name","company":"employer"}. Empty uses all non-internal columns.')
STRUCT = field('struct', 'Fields to research', 'json', {'title': 'Current job title', 'company': 'Current employer'}, True,
               'Output field → description, or {"description":"…","type":"int"}.')
RESEARCH = field('research_plan', 'Research instructions', 'textarea', '', help='Optional instructions about sources or research method.')
TIER = field('tier', 'Research depth', 'select', 'low', options=['low', 'medium'])
CONFIDENCE = field('field_confidence', 'Include field confidence', 'boolean', True)
OVERWRITE = field('overwrite', 'Replace existing values', 'boolean', False, help='Off: fill missing values only.')
CONTINUE = field('continue_on_error', 'Continue after individual row failures', 'boolean', True, help='Failed rows retain an error column; authentication and credit errors always stop the run.')
MODE = field('mode', 'Search for', 'select', 'people', options=['people', 'company'])
LIMIT = field('max_results', 'Maximum results', 'number', 100, minimum=1, maximum=5000)
EXCLUDE = field('exclude_entity_ids', 'Exclude identifiers', 'json', [], help='Up to 1,000 LinkedIn identifiers or company domains.')
KEYS = field('key_columns', 'Key columns', 'json', ['email'], True, 'List of columns, e.g. ["email"]. Rows with missing keys are preserved.')


def block(type, label, group, description, fields, source=False, endpoint=None, docs=None):
    return dict(type=type, label=label, group=group, description=description,
                fields=deepcopy(fields), source=source, endpoint=endpoint,
                docs=DOCS + docs if docs else None)


# Public block identifiers are saved by the UI and resolved by engine/registry.py.
# source=True means the block creates the initial table and must begin the path.
CATALOG = [
    block('read_csv', 'Read CSV', 'Data', 'Load an uploaded CSV or a CSV inside inputs/ or outputs/.', [
        field('dataset_mode', 'Dataset mode', 'select', 'unknown', options=['unknown', 'people', 'companies'], help='Choose the entity represented by each row; unknown preserves flexible CSV workflows.'),
        field('path', 'CSV path', default='inputs/example_workflow.csv', required=True),
        field('delimiter', 'Delimiter', default=',', required=True),
    ], source=True),
    block('save_csv', 'Save CSV', 'Data', 'Save this step to a unique downloadable CSV.', [
        field('filename', 'Filename', default='results.csv', required=True),
    ]),
    block('inspect_dataset', 'Inspect data', 'Data', 'Preview rows and report column types, missing values and distinct counts without changing data.', []),
    block('merge_datasets', 'Merge data', 'Data', 'Append snapshots from earlier blocks in this run to the current table.', [
        field('block_ids', 'Earlier blocks to append', 'snapshots', [], True),
    ]),
    block('filter', 'Filter rows', 'Logic', 'Keep rows matching structured conditions. Legacy Pandas expressions remain available in restricted mode.', [
        field('rules', 'Filter rules', 'json', [{'column': 'email', 'operator': 'is_present', 'value': None}], True,
              'Operators: eq, ne, gt, gte, lt, lte, in, not_in, contains, is_missing, is_present.'),
        field('match', 'Match', 'select', 'all', options=['all', 'any']),
        field('rule', 'Legacy Pandas expression (optional)', 'textarea', '', help='Overrides rules. Supports df column selection, comparisons, isna/notna, isin, and str.strip/contains/lower. No arbitrary Python.'),
    ]),
    block('deduplicate_rows', 'Remove duplicates', 'Logic', 'Remove exact duplicate composite keys; keep rows with missing keys.', [
        KEYS, field('keep', 'Keep occurrence', 'select', 'first', options=['first', 'last']),
    ]),
    block('enrich_lead', 'Enrich people', 'Enrichment', 'Research people with custom fields and optional source guidance.',
          [MAPPING, STRUCT, RESEARCH, TIER, CONFIDENCE, OVERWRITE, CONTINUE], endpoint='/people-intelligence-async', docs='api-reference/endpoint/people-intelligence'),
    block('research_companies', 'Research companies', 'Enrichment', 'Research company attributes from names, domains or LinkedIn URLs.', [
        MAPPING, field('struct', 'Fields to research', 'json', {'industry': 'Primary industry', 'employee_count': {'description': 'Approximate employee count', 'type': 'int'}}, True),
        RESEARCH, TIER, CONFIDENCE, OVERWRITE, CONTINUE,
    ], endpoint='/company-intelligence-async', docs='api-reference/endpoint/company-intelligence'),
    block('find_decision_makers', 'Find decision makers', 'Enrichment', 'Expand each company into people matching a role description; retain company and parent row IDs.', [
        MAPPING, field('people_focus_prompt', 'Who to find', 'textarea', 'Find the CTO and VP of Engineering', True),
        field('struct', 'Company fields', 'json', {'company_name': 'Company name'}, True),
        field('lead_struct', 'Person fields', 'json', {'name': 'Full name', 'title': 'Current title', 'linkedin': 'LinkedIn profile URL'}, True),
        RESEARCH, TIER, CONTINUE,
    ], endpoint='/company-intelligence-async', docs='api-reference/endpoint/company-intelligence'),
    block('find_email', 'Find email', 'Enrichment', 'Find work or personal emails and preserve validation status and email type.', [
        MAPPING, field('mode', 'Email type', 'select', 'PROFESSIONAL', options=['PROFESSIONAL', 'PERSONAL']),
        field('verify_emails', 'Verify discovered emails', 'boolean', True),
        field('only_missing', 'Skip rows with an existing email', 'boolean', True), OVERWRITE, CONTINUE,
    ], endpoint='/find-email-async', docs='api-reference/endpoint/find-email'),
    block('find_phone', 'Find phone', 'Enrichment', 'Find phone numbers; retain the full provider response for inspection.', [
        MAPPING, field('only_missing', 'Skip rows with an existing phone', 'boolean', True), OVERWRITE, CONTINUE,
    ], endpoint='/find-phone-async', docs='api-reference/endpoint/find-phone'),
    block('reverse_email', 'Reverse email', 'Enrichment', 'Resolve person details from an email address.', [
        field('email_column', 'Email column', default='email', required=True), OVERWRITE, CONTINUE,
    ], endpoint='/reverse-email-async', docs='api-reference/endpoint/reverse-email'),
    block('enrich_linkedin', 'Enrich LinkedIn', 'Enrichment', 'Extract requested fields from person or company LinkedIn URLs.', [
        field('linkedin_url_column', 'LinkedIn URL column', default='linkedin', required=True),
        field('struct', 'Fields to extract', 'json', {'title': 'Current title', 'company': 'Current employer'}, True,
              'Output field → plain-English description.'), OVERWRITE, CONTINUE,
    ], endpoint='/enrich-linkedin', docs='api-reference/enrichment/enrich-linkedin-profile'),
    block('qualify_leads', 'Qualify leads', 'Enrichment', 'Evaluate people or companies against weighted criteria using Sixtyfour’s QA endpoint (beta).', [
        MAPPING, field('qualification_criteria', 'Qualification criteria', 'json', [{'criteria_name': 'Fit', 'description': 'Is a B2B software company', 'weight': 1, 'threshold': 0.7}], True,
                       'List of criteria_name, description, weight and optional threshold (0–1 in the current schema).'),
        field('struct', 'Output fields', 'json', {'qualification_score': 'Overall score from 0 to 10', 'qualification_verdict': 'accept, reject, or unknown', 'qualification_reason': 'Explain the decision with evidence'}, True,
              'Descriptions only. QA returns custom fields as strings; numeric scores are parsed when valid.'),
        field('reference_columns', 'Columns containing evidence URLs', 'json', []),
        field('max_tool_calls', 'Research calls per row', 'number', 10, minimum=1, maximum=50), OVERWRITE, CONTINUE,
    ], endpoint='/qa-agent-async', docs='api-reference/endpoint/qa-agent'),
    block('find_prospects', 'Find prospects', 'Search', 'Start a workflow with a natural-language people or company search.', [
        field('query', 'Search description', 'textarea', '', True), MODE, LIMIT, EXCLUDE,
    ], source=True, endpoint='/search/start-deep-search', docs='api-reference/search/search-endpoints'),
    block('search_by_filters', 'Search by filters', 'Search', 'Start a workflow using structured filters; automatically retrieve pages up to the limit.', [
        MODE, field('simple_filters', 'Search filters', 'json', {}, True, 'Use Get search fields and Get field values blocks to discover valid fields and values.'),
        LIMIT, field('page_size', 'Rows per request', 'number', 100, minimum=1, maximum=100), EXCLUDE,
    ], source=True, endpoint='/search/query', docs='api-reference/search/filter-search'),
    block('get_search_fields', 'Get search fields', 'Search', 'List available search fields and operators as a previewable table.', [MODE],
          source=True, endpoint='/search/filter-capabilities', docs='api-reference/search/filter-search'),
    block('get_search_field_values', 'Get field values', 'Search', 'List common values and counts for one searchable field.', [
        MODE, field('field', 'Field name', default='industry', required=True),
        field('top_k', 'Maximum values', 'number', 25, minimum=1, maximum=100),
        field('simple_filters', 'Optional scoping filters', 'json', {}),
    ], source=True, endpoint='/search/filter-field-values', docs='api-reference/search/filter-search'),
]
# Index for backend lookup; CATALOG remains an ordered list for the editor.
BY_TYPE = {item['type']: item for item in CATALOG}
for item in CATALOG:
    if item['group'] == 'Enrichment':
        item['fields'].append(field('max_workers', 'Concurrent rows', 'number', 32,
                                    minimum=1, maximum=32,
                                    help='Maximum simultaneous row lookups. Results retain input order.'))


def normalize_config(block_type, config):
    """Fill defaults, migrate legacy settings, and reject invalid configuration.

    This checks settings before execution. Checks needing actual table columns
    happen in the tool or row processor once its input table exists.
    """
    if block_type not in BY_TYPE:
        raise ValueError('Unknown block type: ' + block_type)
    # Copy mutable defaults so one block's settings cannot alter another block.
    result = {f['key']: deepcopy(f['default']) for f in BY_TYPE[block_type]['fields']}
    result.update(config)
    # Old callers selected names instead of supplying a struct.
    if block_type == 'enrich_lead' and 'fields' in config and 'struct' not in config:
        result['struct'] = {('github_url' if k == 'github' else k): k.replace('_', ' ') for k in config['fields']}
    for f in BY_TYPE[block_type]['fields']:
        value = result[f['key']]
        if f['key'] == 'rules' and result.get('rule'):
            continue
        if f['required'] and (value is None or value == '' or value == []):
            raise ValueError(f['label'] + ' is required')
        if f['kind'] in ('text', 'textarea') and not isinstance(value, str):
            raise ValueError(f['label'] + ' must be text')
        if f['required'] and isinstance(value, str) and not value.strip():
            raise ValueError(f['label'] + ' is required')
        if f['kind'] == 'boolean' and not isinstance(value, bool):
            raise ValueError(f['label'] + ' must be a boolean')
        if f['kind'] == 'select' and value not in f['options']:
            raise ValueError('Invalid ' + f['label'])
        if f['kind'] == 'number' and (isinstance(value, bool) or not isinstance(value, int) or not f['minimum'] <= value <= f['maximum']):
            raise ValueError(f['label'] + ' is outside the allowed integer range')
        if f['kind'] in ('json', 'snapshots') and not isinstance(value, type(f['default'])):
            raise ValueError(f['label'] + ' has the wrong JSON type')
    for key in ('input_mapping',):
        if key in result and any(not isinstance(v, str) or not v for v in result[key].values()):
            raise ValueError('Column mapping values must be column names')
    for key in ('block_ids', 'key_columns', 'reference_columns', 'exclude_entity_ids'):
        if key in result and any(not isinstance(v, str) or not v.strip() for v in result[key]):
            raise ValueError(key + ' must contain non-empty strings')
    if len(result.get('exclude_entity_ids', [])) > 1000:
        raise ValueError('At most 1000 exclusions are supported')
    # Research schemas allow typed descriptions; QA/LinkedIn accept strings only.
    for key in ('struct', 'lead_struct'):
        if key not in result:
            continue
        if not result[key]:
            raise ValueError('Choose at least one output field')
        for name, spec in result[key].items():
            if name.startswith('_'):
                raise ValueError('Output fields cannot start with an underscore')
            if isinstance(spec, str) and spec.strip():
                continue
            if block_type in ('qualify_leads', 'enrich_linkedin'):
                raise ValueError('This endpoint requires string field descriptions')
            if not isinstance(spec, dict) or not isinstance(spec.get('description'), str) or not spec['description'].strip() or spec.get('type') not in ('str','string','int','integer','float','bool','boolean','list','list[str]','list[int]','list[float]','list[bool]','list[boolean]','dict'):
                raise ValueError('Each field requires a description and a supported type')
    if block_type == 'qualify_leads':
        names = set()
        for c in result['qualification_criteria']:
            if not isinstance(c, dict) or not isinstance(c.get('criteria_name'), str) or not c['criteria_name'].strip() or not isinstance(c.get('description'), str) or not c['description'].strip():
                raise ValueError('Each criterion needs a name and description')
            if c['criteria_name'] in names:
                raise ValueError('Criterion names must be unique')
            names.add(c['criteria_name'])
            weight = c.get('weight', 1)
            threshold = c.get('threshold')
            if not isinstance(weight, (int, float)) or not 0 < weight < float('inf'):
                raise ValueError('Criterion weights must be positive finite numbers')
            if threshold is not None and (not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1):
                raise ValueError('Criterion thresholds must be between 0 and 1')
    return result
