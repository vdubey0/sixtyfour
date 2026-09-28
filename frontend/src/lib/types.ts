//on the block catalog
export type BlockType = {
    type: string; //internal identifier
    color: string;
    label: string; //external name
};

//on the canvas after block has been dropped
export type BlockInstance = {
    id: string;
    type: string; //corresponds to type in BlockType
    x: number;
    y: number;
    config: Record<string, unknown>;
};

export type Connection = {
  id: string;
  fromId: string;
  toId: string;
};
export type BlockConfig = Record<string, unknown>;
export type ConfigField = {
  key: string;
  label: string;
  kind: 'text' | 'textarea' | 'json' | 'boolean' | 'number' | 'select' | 'snapshots';
  default: unknown;
  required: boolean;
  help: string;
  options?: string[];
  minimum?: number;
  maximum?: number;
};
export type BlockDefinition = {
  type: string;
  label: string;
  group: string;
  description: string;
  fields: ConfigField[];
  source: boolean;
  endpoint: string | null;
  docs: string | null;
};
export type BlockResult = {
  row_count: number;
  columns: string[];
  preview: Record<string, unknown>[];
  failed_rows: number;
  metadata: Record<string, unknown>;
  downloads: { filename: string; url: string }[];
};

export function defaultConfig(definition: BlockDefinition): BlockConfig {
  return Object.fromEntries(definition.fields.map(field => [field.key, structuredClone(field.default)]));
}

export function parseConfig(definition: BlockDefinition, config: BlockConfig): BlockConfig {
  const parsed = { ...config };
  for (const field of definition.fields) {
    const value = config[field.key] ?? field.default;
    if (field.kind === 'json' && typeof value === 'string') {
      try { parsed[field.key] = JSON.parse(value); }
      catch { throw new Error(`${definition.label}: ${field.label} must be valid JSON.`); }
    } else parsed[field.key] = value;
    if (field.required && !(field.key === 'rules' && config.rule)) {
      const resolved = parsed[field.key];
      if (resolved === null || resolved === undefined || (typeof resolved === 'string' && !resolved.trim()) || (Array.isArray(resolved) && !resolved.length)) {
        throw new Error(`${definition.label}: ${field.label} is required.`);
      }
    }
  }
  return parsed;
}
