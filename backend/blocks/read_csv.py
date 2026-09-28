import csv
from pathlib import Path
import pandas as pd
from backend.blocks.sixtyfour import ROOT


def resolve_csv(path):
    resolved = (ROOT / path).resolve()
    roots = [(ROOT / name).resolve() for name in ('inputs', 'outputs')]
    if not any(root in resolved.parents for root in roots) or resolved.suffix.lower() != '.csv':
        raise ValueError('CSV path must point to a .csv file inside inputs/ or outputs/')
    if not resolved.is_file():
        raise ValueError('CSV file does not exist: ' + path)
    return resolved


def read_csv_header(path, delimiter=','):
    if len(delimiter) != 1:
        raise ValueError('Delimiter must be a single character')
    resolved = resolve_csv(path)
    with resolved.open(encoding='utf-8-sig', newline='') as stream:
        header = next(csv.reader(stream, delimiter=delimiter), [])
    if not header or any(not value.strip() for value in header) or len(set(header)) != len(header):
        raise ValueError('CSV requires unique, non-empty column names')
    return header


def read_csv(path, delimiter=','):
    read_csv_header(path, delimiter)
    resolved = resolve_csv(path)
    # Read strings to preserve phone prefixes, identifiers and leading zeroes.
    return pd.read_csv(resolved, sep=delimiter, encoding='utf-8-sig', dtype=str)


def run_read(df, config, context):
    return read_csv(config['path'], config['delimiter'])
