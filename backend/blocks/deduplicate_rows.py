"""Deduplicate rows block."""
from backend.blocks.sixtyfour import missing


def deduplicate_rows(df, config, context):
    keys = config['key_columns']
    absent = set(keys) - set(df.columns)
    if absent:
        raise ValueError('Missing key columns: ' + ', '.join(sorted(absent)))
    complete = ~df[keys].applymap(missing).any(axis=1)
    duplicates = df.loc[complete].duplicated(subset=keys, keep=config['keep'])
    return df.drop(index=duplicates[duplicates].index).copy()
