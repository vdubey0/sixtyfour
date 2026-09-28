"""Find email block: request construction and response handling."""
from backend.blocks.row_processing import SKIP_ROW, process_rows, require_identifiers
from backend.blocks.sixtyfour import APIError, missing


def request(client, row, lead, config):
    target = 'personal_email' if config['mode'] == 'PERSONAL' else 'email'
    if config.get('only_missing') and not missing(lead.get(target)):
        return SKIP_ROW
    if config.get('overwrite'):
        lead.pop('email', None)
        lead.pop('personal_email', None)
    require_identifiers(lead)
    return client.job('/find-email-async', dict(
        lead=lead, mode=config['mode'], verify_emails=config['verify_emails']))


def parse_response(result):
    values = {}
    for key in ('email', 'personal_email'):
        matches = result.get(key, [])
        if not isinstance(matches, list):
            raise APIError('Unexpected email response: expected a list of tuples')
        if any(not isinstance(m, (list, tuple)) or len(m) != 3 for m in matches):
            raise APIError('Unexpected email tuple format')
        values[key] = ', '.join(str(m[0]) for m in matches if m[0] and m[1] != 'NOT_FOUND') or None
        values[key + '_status'] = ', '.join(dict.fromkeys(str(m[1]) for m in matches)) or 'NOT_FOUND'
        values[key + '_matches'] = [{'address': m[0], 'status': m[1], 'type': m[2]} for m in matches]
    return values


def find_email(df, config, context):
    return process_rows(df, config, context, 'find_email', request, parse_response)
