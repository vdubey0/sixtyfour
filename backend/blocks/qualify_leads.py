"""Qualify leads block: request construction and response handling."""
from backend.blocks.row_processing import process_rows, require_identifiers, provider_values
from backend.blocks.sixtyfour import missing


def request(client, row, lead, config):
    require_identifiers(lead)
    payload = dict(data=lead, qualification_criteria=config['qualification_criteria'],
                   struct=config['struct'], max_tool_calls=config['max_tool_calls'])
    payload['references'] = [{'url': str(row[c]), 'description': c}
                             for c in config['reference_columns'] if not missing(row[c])]
    return client.job('/qa-agent-async', payload)


def parse_response(result):
    values = provider_values(result)
    if 'qualification_score' in values:
        try:
            score = float(values['qualification_score'])
            values['qualification_score'] = score if 0 <= score <= 10 else None
        except (ValueError, TypeError):
            values['qualification_score'] = None
    if 'qualification_verdict' in values:
        verdict = str(values['qualification_verdict']).strip().lower()
        values['qualification_verdict'] = verdict if verdict in ('accept', 'reject') else 'unknown'
    return values


def qualify_leads(df, config, context):
    return process_rows(df, config, context, 'qualify_leads', request, parse_response,
                        required_columns=config['reference_columns'])
