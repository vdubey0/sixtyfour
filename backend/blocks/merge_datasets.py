"""Merge datasets block."""
import pandas as pd


def merge_datasets(df, config, context):
    frames = [df]
    for block_id in dict.fromkeys(config['block_ids']):
        if block_id not in context.snapshots:
            raise ValueError('Merge can only reference blocks already executed in this run')
        frames.append(context.snapshots[block_id])
    result = pd.concat(frames, ignore_index=True, sort=False)
    # Appended snapshots can contain the same row identities; keep lineage but
    # assign fresh unique identities to the combined table.
    if '_row_id' in result:
        result['_parent_row_id'] = result['_row_id']
    result['_row_id'] = [context.block_id + ':' + str(i) for i in range(len(result))]
    return result
