"""Get search field values source block."""
from backend.blocks.sixtyfour import SixtyfourClient
from backend.blocks.search_helpers import records_frame


def get_search_field_values(df, config, context):
    client = SixtyfourClient(context)
    response = client.request('POST', '/search/filter-field-values', config)
    context.metadata = response
    return records_frame(response.get('values', []))
