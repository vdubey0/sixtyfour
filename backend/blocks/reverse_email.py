"""Reverse email block: request construction and response handling."""
from backend.blocks.row_processing import process_rows, provider_values
from backend.blocks.sixtyfour import missing


def request(client, row, lead, config):
    if missing(row[config['email_column']]):
        raise ValueError('Email is missing')
    return client.job('/reverse-email-async', {'lead': {'email': row[config['email_column']]}})


def reverse_email(df, config, context):
    return process_rows(df, config, context, 'reverse_email', request, provider_values,
                        required_columns=[config['email_column']])
