"""Inspect dataset block."""
from backend.blocks.sixtyfour import missing


def inspect_dataset(df, config, context):
    context.metadata = {'profiles': {str(name): {
        'dtype': str(df[name].dtype), 'missing_count': int(df[name].map(missing).sum()),
        'distinct_count': int(df[name].nunique(dropna=True)),
    } for name in df.columns}}
    return df.copy()
