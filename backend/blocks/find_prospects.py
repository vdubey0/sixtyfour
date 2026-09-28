"""Find prospects source block."""
from backend.blocks.sixtyfour import SixtyfourClient, APIError
from backend.blocks.search_helpers import query_pages


def find_prospects(df, config, context):
    client = SixtyfourClient(context)
    response = client.job('/search/start-deep-search', dict(
        query=config['query'], mode=config['mode'], max_results=config['max_results'],
        output_mode='query_only', exclude_entity_ids=config.get('exclude_entity_ids', [])), search=True)
    if not response.get('search_id'):
        raise APIError('Completed search did not return search_id; cannot retrieve rows')
    return query_pages(client, {'search_id': response['search_id']}, config, context)
