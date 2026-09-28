type Link = { source: string; target: string };

// A proposed source → target closes a cycle if target already reaches source.
export function canConnect(connection: Link, edges: Link[]): boolean {
  const { source, target } = connection;
  if (!source || !target || source === target) return false;
  const pending = [target];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop()!;
    if (current === source) return false;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const edge of edges) {
      // These connections are replaced by the editor's single-path rule.
      if (edge.source === source || edge.target === target) continue;
      if (edge.source === current) pending.push(edge.target);
    }
  }
  return true;
}
