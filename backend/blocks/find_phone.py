"""Find phone block: request construction and response handling."""
from backend.blocks.row_processing import SKIP_ROW, process_rows, require_identifiers, provider_values
from backend.blocks.sixtyfour import missing


def request(client, row, lead, config):
    if config.get('only_missing') and not missing(lead.get('phone')):
        return SKIP_ROW
    require_identifiers(lead)
    return client.job('/find-phone-async', dict(lead=lead))


def find_phone(df, config, context):
    return process_rows(df, config, context, 'find_phone', request, provider_values)
