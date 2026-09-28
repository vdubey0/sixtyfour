"""Search source blocks. Pagination stays in the backend, with strict result caps."""
import pandas as pd
from backend.blocks.sixtyfour import APIError, cell


def records_frame(records):
    """Validate record-shaped output and serialize nested fields into table cells."""
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise APIError('Expected a list of search records')
    return pd.DataFrame([{k: cell(v) for k, v in row.items()} for row in records])


def query_pages(client, payload, config, context):
    """Retrieve a search ID or structured-filter query up to the configured row cap.

    Both search tools share this endpoint. The first request carries the search
    settings; subsequent requests carry only the provider-issued cursor.
    """
    exclusions = {'exclude_entity_ids': config.get('exclude_entity_ids', [])}
    payload.update(mode=config['mode'], page_size=config.get('page_size', 100),
                   max_results=config['max_results'], output_shape='workflow', **exclusions)
    records, cursors = [], set()
    # A finite page bound also covers repeated empty pages caused by exclusions.
    for _ in range(10000):
        response = client.request('POST', '/search/query', payload)
        page = response.get('results')
        if not isinstance(page, list):
            raise APIError('Search response is missing results')
        # The final page may exceed our requested limit; retain only the needed rows.
        records.extend(page[:config['max_results'] - len(records)])
        context.progress('Retrieved %s search rows' % len(records))
        context.metadata = {k: v for k, v in response.items() if k != 'results'}
        if response.get('company_filter_truncated'):
            context.progress('Warning: company filter expansion was truncated by Sixtyfour; inspect result metadata.')
        # An empty page can still have more results (for example after exclusions).
        # Trust has_more instead of stopping just because this page is empty.
        if not response.get('has_more') or len(records) >= config['max_results']:
            return records_frame(records)
        cursor = response.get('next_cursor')
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise APIError('Search returned a missing or repeated pagination cursor')
        cursors.add(cursor)
        payload = {'cursor': cursor}  # API requires cursor alone on continuations.
    raise APIError('Search exceeded 10000 pages; narrow the query')
