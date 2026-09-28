"""Enrich lead block: request construction and response handling."""
from backend.blocks.row_processing import process_rows, require_identifiers, structured_values


def request(client, row, lead, config):
    require_identifiers(lead)
    payload = dict(lead_info=lead, struct=config['struct'], tier=config['tier'],
                   field_confidence=config['field_confidence'])
    if config.get('research_plan'):
        payload['research_plan'] = config['research_plan']
    return client.job('/people-intelligence-async', payload)


def enrich_lead(df, config, context):
    return process_rows(df, config, context, 'enrich_lead', request, structured_values)
