"""Thin agent adapters: all business logic remains in the existing blocks."""
import hashlib
import json
import pandas as pd
from backend.blocks.catalog import CATALOG, BY_TYPE, normalize_config
from backend.blocks.filter import filter as filter_rows, validate_rules
from backend.blocks.sixtyfour import clean_value, missing
from backend.engine.compatibility import DatasetSchema, validate_input, output_schema
from backend.engine.registry import get_block_function

METADATA_TOOLS = {'get_search_fields', 'get_search_field_values'}
SOURCES = {'find_prospects', 'search_by_filters'} | METADATA_TOOLS
AGENT_TOOL_DESCRIPTIONS = {
    'filter': (
        'Keep rows using deterministic structured conditions on observed columns and concrete '
        'values: numeric thresholds, exact categories, missing values, or qualification verdicts. '
        'For business meaning or lead fit, use qualify_leads first. Never use keywords in '
        'overviews, descriptions, or summaries as semantic qualification or a preliminary filter. '
        'Text matching requires an explicit literal-text request or an established data guarantee '
        'of exact equivalence to the requested criterion; assumptions and sample patterns do not suffice.'
    ),
    'qualify_leads': (
        'Evaluate people or companies against weighted criteria using Sixtyfour’s QA endpoint (beta). '
        'Prefer this tool for semantic criteria, business characteristics, or lead fit, including '
        'whether a company is SaaS. Produce explicit qualification verdicts for completion-contract '
        'conditions, then filter on those verdicts. Do not replace qualification with free-text keywords.'
    ),
}
CATALOG_FOR_AGENT = [
    dict(item, description=AGENT_TOOL_DESCRIPTIONS.get(item['type'], item['description']))
    for item in CATALOG if item['type'] not in ('read_csv', 'save_csv')
]
ALLOWED = {item['type'] for item in CATALOG_FOR_AGENT}


def digest(value):
    return hashlib.sha256(json.dumps(clean_value(value), sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def fingerprint(df):
    # Ordering, row IDs, raw payloads, and history timestamps cannot fake progress.
    columns = sorted(c for c in df.columns if not c.startswith('_'))
    rows = sorted(digest(row) for row in df[columns].to_dict('records'))
    failures = int(df['_status'].eq('failed').sum()) if '_status' in df else 0
    return digest({'columns': columns, 'rows': rows, 'failed_rows': failures})


def preview(df, rows=20):
    columns = [c for c in df.columns if not c.startswith('_')]
    return {'row_count': len(df), 'columns': columns,
            'preview': clean_value(df[columns].head(rows).to_dict('records'))}


def observation(df):
    columns = [c for c in df.columns if not c.startswith('_')][:60]
    sample = clean_value(df[columns].head(5).to_dict('records'))
    return {'row_count': len(df), 'columns': columns,
            'columns_truncated': len(df.columns) > 60,
            'missing_counts': {c: int(df[c].map(missing).sum()) for c in columns},
            'preview': [{k: str(v)[:400] if v is not None else None for k, v in row.items()} for row in sample]}


def prepare(run, decision):
    kind = decision.tool
    if kind not in ALLOWED:
        raise ValueError('Choose a tool from the agent catalog.')
    args = json.loads(decision.arguments_json or '{}')
    if not isinstance(args, dict):
        raise ValueError('Tool arguments must be a JSON object.')
    fields = {f['key'] for f in BY_TYPE[kind]['fields']}
    if set(args) - fields:
        raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(set(args) - fields)))
    if kind == 'filter' and args.get('rule'):
        raise ValueError('Agent mode uses structured filter rules only.')
    config = normalize_config(kind, args)
    if kind == 'filter':
        validate_rules(config['rules'])
    if kind in SOURCES:
        df, mode, ref = pd.DataFrame(), 'unknown', None
    else:
        ref = decision.input_dataset or 'current'
        ref = run.current if ref == 'current' else ref
        if ref not in run.context.snapshots:
            raise ValueError('Select an existing dataset snapshot.')
        df = run.context.snapshots[ref]
        mode = run.context.schema_snapshots[ref].mode
    if config.get('max_results', 0) > run.limits.max_rows:
        raise ValueError('Requested result count exceeds the agent row limit.')
    if kind == 'merge_datasets':
        for item in config['block_ids']:
            if item not in run.context.snapshots:
                raise ValueError('Merge references an unknown snapshot: ' + item)
        if len(df) + sum(len(run.context.snapshots[i]) for i in config['block_ids']) > run.limits.max_rows:
            raise ValueError('Merged dataset would exceed the row limit.')
    schema = DatasetSchema(mode, set(df.columns))
    validate_input(kind, config, schema)
    # Reject provably redundant lookups before submitting a paid tool call.
    # Explicit refreshes and verification may need new evidence despite populated cells.
    targets = []
    if not config.get('overwrite'):
        if kind in ('enrich_lead', 'research_companies', 'enrich_linkedin'):
            targets = list(config['struct'])
        elif kind in ('find_email', 'find_phone') and config.get('only_missing'):
            target = 'phone'
            if kind == 'find_email':
                target = 'personal_email' if config['mode'] == 'PERSONAL' else 'email'
            targets = [config.get('input_mapping', {}).get(target, target)]
    if targets and all(c in df and not df[c].map(missing).any() for c in targets):
        raise ValueError('Unnecessary tool: all requested fields are already present in the input dataframe. '
                         'Reuse existing values and address only unmet completion criteria; finish if satisfied.')
    predicted = output_schema(kind, config, schema, run.context.schema_snapshots)
    # Snapshot names do not evade duplicate detection for identical contents.
    normalized = dict(config)
    if kind == 'merge_datasets':
        normalized['block_ids'] = sorted(fingerprint(run.context.snapshots[i]) for i in config['block_ids'])
    signature = digest([kind, normalized, mode, fingerprint(df)])
    if signature in run.attempted:
        raise ValueError('Duplicate action: this tool and configuration already ran on identical input. Choose a different useful action or stop.')
    return kind, config, df, mode, predicted, signature


def execute_prepared(prepared, context):
    kind, config, df, mode, _, _ = prepared
    context.check_cancelled()
    context.mode = mode
    context.metadata = {}
    data = df.copy(deep=True)
    data.attrs = {}
    result = get_block_function(kind)(data, config, context)
    context.check_cancelled()
    if not isinstance(result, pd.DataFrame) or not result.columns.is_unique:
        raise ValueError('Tool must return a table with unique column names.')
    result = result.reset_index(drop=True)
    if '_row_id' not in result or not result['_row_id'].is_unique or result['_row_id'].isna().any():
        result['_row_id'] = [context.block_id + ':' + str(i) for i in range(len(result))]
    return result


def validate_contract(contract):
    if contract.max_rows is not None and contract.max_rows < contract.min_rows:
        raise ValueError('Completion maximum cannot be smaller than its minimum.')
    for condition in contract.conditions:
        validate_rules([{'column': condition.column, 'operator': condition.operator,
                         'value': json.loads(condition.value_json)}])


def check_contract(contract, df, mode):
    if contract is None or df is None:
        return {'satisfied': False, 'issues': ['No completion contract or result dataset yet.']}
    issues = list(contract.unresolved_requirements)
    if contract.mode != 'unknown' and contract.mode != mode:
        issues.append('Expected %s data; current mode is %s.' % (contract.mode, mode))
    if len(df) < contract.min_rows:
        issues.append('Found %s of at least %s requested rows.' % (len(df), contract.min_rows))
    if contract.max_rows is not None and len(df) > contract.max_rows:
        issues.append('Result exceeds the requested maximum of %s rows.' % contract.max_rows)
    eligible = df
    for column in contract.required_columns:
        if column not in eligible:
            issues.append('Missing required column: ' + column)
            continue
        absent = int(eligible[column].map(missing).sum())
        if absent:
            issues.append('%s rows are missing %s.' % (absent, column))
    if contract.unique_by:
        if set(contract.unique_by) - set(df.columns):
            issues.append('Missing deduplication key columns.')
        else:
            identified = df.loc[~df[contract.unique_by].apply(lambda s: s.map(missing)).any(axis=1)]
            duplicates = int(identified.duplicated(subset=contract.unique_by).sum())
            if duplicates:
                issues.append('%s duplicate rows remain.' % duplicates)
    for condition in contract.conditions:
        try:
            matched = filter_rows(df, {'match': 'all', 'rules': [
                {'column': condition.column, 'operator': condition.operator,
                 'value': json.loads(condition.value_json)}]})
            if len(matched) != len(df):
                issues.append('%s rows do not satisfy %s %s %s.' %
                              (len(df) - len(matched), condition.column, condition.operator, condition.value_json))
        except (ValueError, KeyError, TypeError):
            issues.append('Cannot verify condition on ' + condition.column)
    if '_status' in df and df['_status'].eq('failed').any():
        issues.append('The result still includes failed rows.')
    return {'satisfied': not issues, 'issues': issues}
