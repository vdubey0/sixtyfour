"""Enrich linkedin block: request construction and response handling."""
from urllib.parse import urlparse
from backend.blocks.row_processing import process_rows, provider_values


def request(client, row, lead, config):
    url = str(row[config['linkedin_url_column']] or '')
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not (parsed.hostname == 'linkedin.com' or (parsed.hostname or '').endswith('.linkedin.com')) or not parsed.path.startswith(('/in/', '/company/')):
        raise ValueError('Expected a full LinkedIn person or company URL')
    payload = dict(linkedin_url=url, struct=config['struct'])
    return client.request('POST', '/enrich-linkedin', payload, timeout=client.job_timeout)


def enrich_linkedin(df, config, context):
    return process_rows(df, config, context, 'enrich_linkedin', request, provider_values,
                        required_columns=[config['linkedin_url_column']])
