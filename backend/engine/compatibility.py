"""Dataset contracts, independent of HTTP, scheduling, and provider requests.

Unknown provider columns are deferred to runtime, never assumed to exist. Mode
is semantic: CSV defaults to unknown for backwards compatibility. Explicit input
mappings remain the authority for provider identifier names.
"""
import ast
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class DatasetSchema:
    mode: str = 'unknown'
    columns: set = field(default_factory=set)
    open_columns: bool = False


# Every registered block must have a contract; missing contracts fail closed.
CONTRACTS = {
    'read_csv': 'source', 'find_prospects': 'source', 'search_by_filters': 'source',
    'get_search_fields': 'source', 'get_search_field_values': 'source',
    'save_csv': 'table', 'inspect_dataset': 'table', 'filter': 'table',
    'deduplicate_rows': 'table', 'merge_datasets': 'table',
    'enrich_lead': 'people', 'find_email': 'people', 'find_phone': 'people',
    'reverse_email': 'people', 'research_companies': 'companies',
    'find_decision_makers': 'companies', 'enrich_linkedin': 'entity',
    'qualify_leads': 'entity',
}


def company_column(name):
    return name if name.startswith('company_') else 'company_' + name


def company_columns(columns):
    public = [c for c in columns if not c.startswith('_')]
    names = [company_column(c) for c in public]
    if len(names) != len(set(names)):
        raise ValueError('Company columns collide after prefixing; rename ambiguous columns before Find decision makers')
    return set(names)


def required_columns(kind, config):
    needed = set(config.get('input_mapping', {}).values())
    for key in ('email_column', 'linkedin_url_column'):
        if key in config:
            needed.add(config[key])
    needed.update(config.get('reference_columns', []))
    needed.update(config.get('key_columns', []))
    if kind == 'filter':
        if config.get('rule', '').strip():
            # Extract literal df selections without executing the expression.
            tree = ast.parse(config['rule'], mode='eval')
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'df':
                    part = node.slice.value if isinstance(node.slice, ast.Index) else node.slice
                    try:
                        value = ast.literal_eval(part)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(value, str):
                        needed.add(value)
                    elif isinstance(value, list):
                        needed.update(v for v in value if isinstance(v, str))
        else:
            needed.update(rule['column'] for rule in config['rules'])
    return needed


def validate_input(kind, config, schema):
    contract = CONTRACTS[kind]
    if contract == 'source':
        return
    if schema.mode == 'metadata' and contract != 'table':
        raise ValueError('Search metadata cannot be used as people or company data')
    if contract == 'people' and schema.mode == 'companies':
        raise ValueError('This block requires people; add Find decision makers first')
    if contract == 'companies' and schema.mode == 'people':
        if kind == 'find_decision_makers':
            raise ValueError('Find decision makers requires company rows')
        if not config.get('input_mapping'):
            raise ValueError('Company research on people requires explicit company column mappings')
    absent = required_columns(kind, config) - schema.columns
    if absent and not schema.open_columns:
        raise ValueError('Missing columns: ' + ', '.join(sorted(absent)))
    if kind == 'find_decision_makers':
        company = company_columns(schema.columns)
        overlap = company & set(config['lead_struct'])
        if overlap:
            raise ValueError('Person fields collide with company fields: ' + ', '.join(sorted(overlap)))


def output_schema(kind, config, schema, snapshots):
    result = DatasetSchema(schema.mode, set(schema.columns), schema.open_columns)
    if kind == 'read_csv':
        from backend.blocks.read_csv import read_csv_header
        result = DatasetSchema(config.get('dataset_mode', 'unknown'), set(read_csv_header(config['path'], config['delimiter'])))
    elif kind in ('find_prospects', 'search_by_filters'):
        result = DatasetSchema('people' if config['mode'] == 'people' else 'companies', set(), True)
    elif kind in ('get_search_fields', 'get_search_field_values'):
        result = DatasetSchema('metadata', set(), True)
    elif kind == 'find_decision_makers':
        result = DatasetSchema('people', company_columns(schema.columns) | set(config['lead_struct']) | {'_parent_row_id'}, True)
    elif kind == 'merge_datasets':
        for ref in config['block_ids']:
            other = snapshots[ref]
            if result.mode != other.mode:
                raise ValueError('Merge requires matching dataset modes (including unknown)')
            result.columns.update(other.columns)
            result.open_columns |= other.open_columns
    elif kind in ('enrich_lead', 'research_companies', 'enrich_linkedin', 'qualify_leads'):
        result.columns.update(config['struct'])
        result.open_columns = True
    elif kind == 'find_email':
        result.columns.update({'email', 'personal_email', 'email_status', 'personal_email_status', 'email_matches', 'personal_email_matches'})
    elif kind in ('find_phone', 'reverse_email'):
        result.open_columns = True
    elif kind == 'filter' and config.get('rule', '').strip():
        # Legacy table selection may drop columns; runtime resolves its schema.
        result = DatasetSchema(schema.mode, set(), True)
    result.columns.add('_row_id')
    return result


def validate_workflow(order):
    schema, snapshots = DatasetSchema(), {}
    for block in order:
        try:
            validate_input(block.type, block.config, schema)
            schema = output_schema(block.type, block.config, schema, snapshots)
            snapshots[block.id] = schema
        except (ValueError, SyntaxError, KeyError) as exc:
            raise ValueError('%s (%s): %s' % (block.id, block.type, exc)) from exc


def validate_row(kind, row, lead, config, mode='unknown'):
    """Value guards run inside existing row error handling, before any request."""
    from backend.blocks.sixtyfour import missing
    if row.get('_entity_mode') == 'companies':
        raise ValueError('Company discovery failed for this row; no person is available')
    if kind == 'reverse_email':
        value = row[config['email_column']]
        if not isinstance(value, str) or not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', value.strip()):
            raise ValueError('Expected one email address; select or expand multiple matches first')
    if kind == 'enrich_linkedin':
        value = row[config['linkedin_url_column']]
        parsed = urlparse(value if isinstance(value, str) else '')
        entity = 'people' if parsed.path.startswith('/in/') else 'companies' if parsed.path.startswith('/company/') else None
        if parsed.scheme not in ('http', 'https') or not (parsed.hostname == 'linkedin.com' or (parsed.hostname or '').endswith('.linkedin.com')) or not entity:
            raise ValueError('Expected a full LinkedIn person or company URL')
        if mode in ('people', 'companies') and entity != mode:
            raise ValueError('LinkedIn URL does not match the dataset mode')
    for column in config.get('reference_columns', []):
        value = row[column]
        if not missing(value):
            parsed = urlparse(str(value))
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                raise ValueError('Invalid reference URL in ' + column)
