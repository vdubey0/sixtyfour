"""Research companies block: request construction and response handling."""
from backend.blocks.row_processing import process_rows, require_identifiers, structured_values


def request(client, row, lead, config):
    require_identifiers(lead)
    payload = dict(target_company=lead, struct=config['struct'], tier=config['tier'],
                   field_confidence=config['field_confidence'])
    if config.get('research_plan'):
        payload['research_plan'] = config['research_plan']
    return client.job('/company-intelligence-async', payload)


def research_companies(df, config, context):
    return process_rows(df, config, context, 'research_companies', request, structured_values)
