"""Validate the editor's single-path graph before any work or API spending."""
from backend.blocks.catalog import BY_TYPE, normalize_config
from backend.blocks.filter import validate_rules
from backend.blocks.save_csv import valid_filename
from backend.schemas import ExecuteResponse
from backend.engine.compatibility import validate_workflow


def failure(message):
    """Package a validation error for the HTTP layer without starting a run."""
    return ExecuteResponse(status='error', execution_order=[], error=message)


def create_flow(blocks, connections):
    """Return blocks in execution order, or an ExecuteResponse describing an error.

    A valid graph is one connected path starting with a source block. This also
    fills defaults into each block config so tools receive normalized settings.
    """
    if not blocks:
        return failure('No blocks provided')
    by_id = {block.id: block for block in blocks}
    if len(by_id) != len(blocks):
        return failure('Block IDs must be unique')
    # At most one predecessor/successor: branching and joining are not supported.
    incoming, outgoing = {}, {}
    for edge in connections:
        if edge.fromId not in by_id or edge.toId not in by_id:
            return failure('Connection references an unknown block')
        if edge.fromId == edge.toId:
            return failure('A block cannot connect to itself')
        if edge.fromId in outgoing or edge.toId in incoming:
            return failure('Use one incoming and one outgoing connection per block')
        outgoing[edge.fromId] = edge.toId
        incoming[edge.toId] = edge.fromId
    # The starting node has no incoming edge; canvas position is irrelevant.
    starts = [b.id for b in blocks if b.id not in incoming]
    if len(starts) != 1:
        return failure('Connect all blocks in one path with exactly one starting block')
    # Follow edges and remember visited nodes to detect loops and disconnected nodes.
    order, seen = [], set()
    current = starts[0]
    while current is not None:
        if current in seen:
            return failure('Cycle detected in workflow')
        seen.add(current)
        order.append(by_id[current])
        current = outgoing.get(current)
    if len(seen) != len(blocks):
        return failure('Every block must be reachable; disconnected blocks or cycles are not allowed')
    # Check every config before execution can make a paid API request.
    try:
        for i, block in enumerate(order):
            block.config = normalize_config(block.type, block.config)
            source = BY_TYPE[block.type]['source']
            if i == 0 and not source:
                raise ValueError('Start with Read CSV or a Search source block')
            if i > 0 and source:
                raise ValueError('Source blocks must be first; they cannot replace an existing table')
            if block.type == 'save_csv':
                valid_filename(block.config['filename'])
            if block.type == 'read_csv' and len(block.config['delimiter']) != 1:
                raise ValueError('Delimiter must be one character')
            if block.type == 'filter' and not block.config.get('rule'):
                validate_rules(block.config['rules'])
            if block.type == 'merge_datasets':
                # Merge appends saved snapshots, so referenced blocks must already have run.
                earlier = {b.id for b in order[:i]}
                if any(ref not in earlier for ref in block.config['block_ids']):
                    raise ValueError('Merge references must be earlier blocks in the connected path')
        validate_workflow(order)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return failure(str(exc))
    # Previews are useful without a final Save block; existing CSV chains still work.
    return order
