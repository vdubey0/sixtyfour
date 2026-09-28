import type { BlockConfig, BlockDefinition } from '../lib/types';

type Props = {
  definition: BlockDefinition;
  config: BlockConfig;
  patch: (config: BlockConfig) => void;
  earlierBlocks: { id: string; label: string }[];
  upload: (file: File) => void;
  uploading: boolean;
};

export function BlockSettings({ definition, config, patch, earlierBlocks, upload, uploading }: Props) {
  return <>
    <p className="field-help">{definition.description}</p>
    {definition.docs && <a className="docs-link" href={definition.docs} target="_blank" rel="noreferrer">API documentation ↗</a>}
    {definition.type === 'read_csv' && <label className="upload-field">
      <span className="input-label">{uploading ? 'Uploading…' : 'Upload CSV (UTF-8, up to 10 MB)'}</span>
      <input type="file" accept=".csv,text/csv" disabled={uploading} onChange={event => {
        const file = event.target.files?.[0];
        if (file) upload(file);
        event.target.value = '';
      }} />
    </label>}
    {definition.fields.map(field => {
      const value = config[field.key] ?? field.default;
      const id = `setting-${field.key}`;
      const isSearchFilters = definition.type === 'search_by_filters' && field.key === 'simple_filters';
      // Keep reference examples out of config; an empty filter object only looks blank.
      const emptyFilters = isSearchFilters && (
        (typeof value === 'string' && /^\s*\{\s*\}\s*$/.test(value))
        || (typeof value === 'object' && value !== null
          && !Array.isArray(value) && Object.keys(value).length === 0)
      );
      const filterExample = isSearchFilters ? JSON.stringify(config.mode === 'company'
        ? { hq_country_iso2: { $eq: 'US' }, employees_count: { $gte: 100, $lte: 5000 } }
        : { headline: { $match: 'engineer' } }, null, 2) : undefined;
      let jsonError = '';
      if (field.kind === 'json' && typeof value === 'string') {
        try { JSON.parse(value); } catch { jsonError = 'Enter valid JSON before running.'; }
      }
      return <div className="config-field" key={field.key}>
        {field.kind === 'boolean' ? <label className="checkbox-field" htmlFor={id}>
          <input id={id} type="checkbox" checked={value === true} onChange={event => patch({ [field.key]: event.target.checked })} />
          {field.label}
        </label> : <>
          <label className="input-label" htmlFor={id}>{field.label}{field.required ? ' *' : ''}</label>
          {field.kind === 'select' ? <select id={id} className="text-input" value={String(value)} onChange={event => patch({ [field.key]: event.target.value })}>
            {field.options?.map(option => <option key={option} value={option}>{option}</option>)}
          </select> : field.kind === 'snapshots' ? <div className="snapshot-options" id={id}>
            {earlierBlocks.length ? earlierBlocks.map(block => <label className="checkbox-field" key={block.id}>
              <input type="checkbox" checked={Array.isArray(value) && value.includes(block.id)} onChange={event => {
                const previous = Array.isArray(value) ? value : [];
                patch({ [field.key]: event.target.checked ? [...previous, block.id] : previous.filter(id => id !== block.id) });
              }} />{block.label}
            </label>) : <p className="field-help">Connect this block after the steps you want to append.</p>}
          </div> : field.kind === 'textarea' || field.kind === 'json' ? <textarea
            id={id} className={`text-input ${field.kind === 'json' ? 'code-input' : 'description-input'}`}
            value={emptyFilters ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
            placeholder={filterExample}
            spellCheck={field.kind !== 'json'} aria-invalid={!!jsonError}
            onChange={event => patch({ [field.key]: isSearchFilters && !event.target.value.trim() ? {} : event.target.value })}
          /> : <input id={id} className="text-input" type={field.kind === 'number' ? 'number' : 'text'}
            value={String(value)} min={field.minimum} max={field.maximum} step={field.kind === 'number' ? 1 : undefined}
            onChange={event => patch({ [field.key]: field.kind === 'number' ? (event.target.value === '' ? '' : Number(event.target.value)) : event.target.value })} />}
        </>}
        {jsonError && <p className="config-error">{jsonError}</p>}
        {isSearchFilters && <p className="field-help">Grey text is an example only. Enter your own filters to apply them.</p>}
        {field.help && <p className="field-help">{field.help}</p>}
      </div>;
    })}
  </>;
}
