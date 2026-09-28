"""Find decision makers block: request construction and response handling."""
from backend.blocks.row_processing import process_rows, require_identifiers
from backend.blocks.sixtyfour import APIError, cell
from backend.engine.compatibility import company_column, company_columns


def request(client, row, lead, config):
    require_identifiers(lead)
    payload = dict(target_company=lead, struct=config['struct'], tier=config['tier'],
                   find_people=True, people_focus_prompt=config['people_focus_prompt'],
                   lead_struct=config['lead_struct'])
    if config.get('research_plan'):
        payload['research_plan'] = config['research_plan']
    return client.job('/company-intelligence-async', payload)


def person_list(result):
    # Public success schemas are open-ended; retain raw output on unknown shapes.
    for container in (result, result.get('structured_data', {})):
        if isinstance(container, dict):
            for key in ('leads', 'people', 'associated_people'):
                if key in container:
                    if not isinstance(container[key], list) or any(not isinstance(p, dict) for p in container[key]):
                        raise APIError('Unexpected people collection in company response')
                    return container[key]
    raise APIError('Company response has no recognized people collection. Inspect _raw_response; no people were guessed.')


def expand_rows(row, result):
    rows = []
    company_columns(row)
    for i, person in enumerate(person_list(result)):
        data = person.get('structured_data', person)
        if not isinstance(data, dict):
            raise APIError('Unexpected discovered person shape')
        expanded = {company_column(k): cell(v) for k, v in row.items() if not k.startswith('_')}
        if set(expanded) & {k for k in data if not k.startswith('_')}:
            raise APIError('Person fields collide with company fields')
        expanded.update({k: cell(v) for k, v in data.items() if not k.startswith('_')})
        expanded.update(_row_id=str(row['_row_id']) + ':' + str(i),
                        _parent_row_id=row['_row_id'], _status='completed', _entity_mode='people',
                        _raw_response=row['_raw_response'], _history=row.get('_history'))
        rows.append(expanded)
    return rows


def find_decision_makers(df, config, context):
    return process_rows(df, config, context, 'find_decision_makers', request, None,
                        expand_rows=expand_rows,
                        output_columns=['_row_id', '_parent_row_id'] + sorted(company_columns(df.columns)) + list(config['lead_struct']) + ['_status', '_error'])
