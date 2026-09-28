"""Get search fields source block."""
from backend.blocks.sixtyfour import SixtyfourClient
from backend.blocks.search_helpers import records_frame


def get_search_fields(df, config, context):
    client = SixtyfourClient(context)
    response = client.request('GET', '/search/filter-capabilities', params={'mode': config['mode']})
    context.metadata = response
    return records_frame(response.get('fields', []))
