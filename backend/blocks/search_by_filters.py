"""Search by filters source block."""
from backend.blocks.sixtyfour import SixtyfourClient
from backend.blocks.search_helpers import query_pages


def search_by_filters(df, config, context):
    client = SixtyfourClient(context)
    return query_pages(client, {'simple_filters': config['simple_filters']}, config, context)
