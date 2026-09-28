import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
  Controls,
  MiniMap,
  Handle,
  Position,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  MarkerType,
  type Edge,
  type Node,
  type NodeProps,
  type Connection,
} from "@xyflow/react";
import {
  Workflow,
  FileInput,
  FileOutput,
  Sparkles,
  Mail,
  Building2,
  Users,
  Phone,
  ScanSearch,
  Table2,
  Combine,
  CopyMinus,
  BadgeCheck,
  Link,
  Download,
  ListFilter,
  Play,
  Plus,
  ArrowRight,
  X,
  Trash2,
  Check,
  LoaderCircle,
  AlertCircle,
  Terminal,
  Search,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { canConnect } from "../lib/connections";

import { BlockSettings } from "./BlockSettings";
import { ResizableInspector } from "./ResizableInspector";
import { defaultConfig, parseConfig, type BlockConfig, type BlockDefinition, type BlockResult } from "../lib/types";

const CatalogContext = createContext<BlockDefinition[]>([]);
const icons = { read_csv: FileInput, save_csv: FileOutput, enrich_lead: Sparkles,
  find_email: Mail, filter: ListFilter, research_companies: Building2,
  find_decision_makers: Users, find_phone: Phone, reverse_email: ScanSearch,
  enrich_linkedin: Link, qualify_leads: BadgeCheck, find_prospects: Search,
  search_by_filters: Search, get_search_fields: Table2, get_search_field_values: Table2,
  inspect_dataset: Table2, merge_datasets: Combine, deduplicate_rows: CopyMinus };
type Config = BlockConfig;
type Status = "idle" | "pending" | "running" | "done" | "error";
type FlowNode = Node<
  { blockType: string; config: Config; status: Status },
  "block"
>;
type Log = { kind: "info" | "success" | "error"; message: string };
function WorkflowNode({ data, selected }: NodeProps<FlowNode>) {
  const catalog = useContext(CatalogContext);
  const meta = catalog.find((item) => item.type === data.blockType);
  const Icon = icons[data.blockType as keyof typeof icons] || Workflow;
  if (!meta) return null;
  const detail = String(data.config.path || data.config.query || meta.description);
  return (
    <div
      className={`flow-node ${selected ? "selected" : ""} status-${data.status}`}
    >
      <Handle type="target" position={Position.Left} title="Input: connect from another block’s output" aria-label={`${meta.label} input`} />
      <div className="node-heading">
        <span className={`icon-tile purple`}>
          <Icon size={19} />
        </span>
        <div>
          <span className="eyebrow">{meta.group}</span>
          <strong>{meta.label}</strong>
        </div>
        <span className="node-status">
          {data.status === "done" ? (
            <Check size={16} />
          ) : data.status === "running" ? (
            <LoaderCircle size={16} className="spin" />
          ) : data.status === "error" ? (
            <AlertCircle size={16} />
          ) : null}
        </span>
      </div>
      <div className="node-detail">{detail}</div>
      <Handle type="source" position={Position.Right} title="Output: drag to another block’s input, or click both handles" aria-label={`${meta.label} output`} />
    </div>
  );
}
function WorkflowEdge(props: EdgeProps) {
  const { setEdges } = useReactFlow();
  const [path, labelX, labelY] = getSmoothStepPath(props);
  return <>
    <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={props.style} interactionWidth={24} />
    <EdgeLabelRenderer>
      <button
        className="edge-delete nodrag nopan"
        style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        aria-label="Delete connection"
        title="Delete connection"
        disabled={props.data?.locked === true}
        onClick={(event) => {
          event.stopPropagation();
          setEdges(previous => previous.filter(edge => edge.id !== props.id));
        }}
      ><X size={12} /></button>
    </EdgeLabelRenderer>
  </>;
}
const edgeTypes = { workflow: WorkflowEdge };
const nodeTypes = { block: WorkflowNode };
const edgeOptions = {
  type: "workflow",
  markerEnd: { type: MarkerType.ArrowClosed, color: "#858585" },
  style: { stroke: "#858585", strokeWidth: 1.7 },
};

function Editor() {
  const [catalog, setCatalog] = useState<BlockDefinition[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [apiConfigured, setApiConfigured] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<Record<string, BlockResult>>({});
  const [resultId, setResultId] = useState("");
  const [cellDetail, setCellDetail] = useState<{ column: string; value: string } | null>(null);
  const [panelTab, setPanelTab] = useState<"logs" | "results">("logs");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    fetch("/api/blocks", { signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error(`Backend returned ${response.status}`);
      const data = await response.json();
      setCatalog(data.blocks);
      setApiConfigured(data.api_configured);
    }).catch(error => { if (!abort.signal.aborted) setCatalogError(String(error)); });
    return () => { abort.abort(); controller.current?.abort(); };
  }, []);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<Log[]>([]);
  const [showLogs, setShowLogs] = useState(true);
  const { screenToFlowPosition, fitView } = useReactFlow<FlowNode>();
  const selected = nodes.find((node) => node.id === selectedId);
  const selectedMeta = catalog.find(
    (item) => item.type === selected?.data.blockType,
  );
  const addBlock = (type: string, position?: { x: number; y: number }) => {
    if (running) return;
    const id = crypto.randomUUID();
    const definition = catalog.find(item => item.type === type);
    if (!definition) return;
    const config = defaultConfig(definition);
    setNodes((previous) => [
      ...previous.map((node) => ({ ...node, selected: false })),
      {
        id,
        type: "block",
        position: position ?? {
          x: 100 + nodes.length * 55,
          y: 120 + nodes.length * 95,
        },
        data: { blockType: type, config, status: "idle" },
        selected: true,
      },
    ]);
    setSelectedId(id);
  };
  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((previous) => canConnect(connection, previous) ?
        addEdge(
          connection,
          previous.filter(
            (edge) =>
              edge.source !== connection.source &&
              edge.target !== connection.target,
          ),
        ) : previous,
      );
    },
    [setEdges],
  );
  const patchConfig = (patch: Config) =>
    setNodes((previous) =>
      previous.map((node) =>
        node.id === selectedId
          ? {
              ...node,
              data: { ...node.data, config: { ...node.data.config, ...patch } },
            }
          : node,
      ),
    );
  const addLog = (kind: Log["kind"], message: string) =>
    setLogs((previous) => [...previous, { kind, message }]);
  const setStatus = (id: string, status: Status) =>
    setNodes((previous) =>
      previous.map((node) =>
        node.id === id ? { ...node, data: { ...node.data, status } } : node,
      ),
    );
  const validate = () => {
    const starts = nodes.filter(node => !edges.some(edge => edge.target === node.id));
    if (starts.length !== 1) return "Connect all blocks into one path.";
    const visited = new Set<string>();
    let current: FlowNode | undefined = starts[0];
    while (current && !visited.has(current.id)) {
      visited.add(current.id);
      const definition = catalog.find(item => item.type === current!.data.blockType);
      if (!definition) return "Block definitions are unavailable.";
      if (visited.size === 1 && !definition.source) return "Start with Read CSV or a Search source.";
      if (visited.size > 1 && definition.source) return "Source blocks must be first in a workflow.";
      try { parseConfig(definition, current.data.config); }
      catch (error) { return error instanceof Error ? error.message : "Invalid configuration."; }
      const next = edges.find(edge => edge.source === current!.id);
      current = nodes.find(node => node.id === next?.target);
    }
    if (current || visited.size !== nodes.length) return "Connect every block in one path without loops.";
  };
  const earlierBlocks = (() => {
    const ids: string[] = [];
    let id = selectedId;
    while (id) {
      const edge = edges.find(edge => edge.target === id);
      if (!edge || ids.includes(edge.source)) break;
      ids.unshift(edge.source);
      id = edge.source;
    }
    return ids.map((id, index) => ({ id, label: `${index + 1}. ${catalog.find(item => item.type === nodes.find(node => node.id === id)?.data.blockType)?.label || "Block"}` }));
  })();
  const upload = async (file: File) => {
    const targetId = selectedId;
    if (file.size > 10 * 1024 * 1024) { addLog("error", "CSV uploads are limited to 10 MB."); return; }
    setUploading(true);
    try {
      const response = await fetch("/api/uploads", { method: "POST", body: file, headers: { "Content-Type": "text/csv" } });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Upload failed");
      setNodes(previous => previous.map(node => node.id === targetId ? { ...node, data: { ...node.data, config: { ...node.data.config, path: data.path } } } : node));
      addLog("success", `Uploaded ${file.name}`);
    } catch (error) { addLog("error", error instanceof Error ? error.message : "Upload failed"); }
    finally { setUploading(false); }
  };
  const runFlow = async () => {
    if (running) return;
    setShowLogs(true);
    setPanelTab("logs");
    setLogs([]);
    const error = validate();
    if (error) {
      addLog("error", error);
      return;
    }
    setRunning(true);
    setResults({});
    setCellDetail(null);
    setResultId("");
    controller.current = new AbortController();
    setNodes((previous) =>
      previous.map((node) => ({
        ...node,
        data: { ...node.data, status: "pending" },
      })),
    );
    addLog("info", "Workflow started. Waiting for execution updates…");
    let finished = false;
    try {
      const response = await fetch("/api/execute_stream", {
        method: "POST",
        signal: controller.current.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          blocks: nodes.map((node) => ({
            id: node.id,
            type: node.data.blockType,
            config: parseConfig(catalog.find(item => item.type === node.data.blockType)!, node.data.config),
          })),
          connections: edges.map((edge) => ({
            fromId: edge.source,
            toId: edge.target,
          })),
        }),
      });
      if (!response.ok)
        throw new Error(
          `Backend returned ${response.status} ${response.statusText}`,
        );
      if (!response.body) throw new Error("No execution stream received.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const processLine = (line: string) => {
        if (!line.trim()) return;
        const event = JSON.parse(line);
        const label =
          catalog.find(
            (item) =>
              item.type ===
              nodes.find((node) => node.id === event.blockId)?.data.blockType,
          )?.label || "Block";
        if (event.event === "block_running") {
          setStatus(event.blockId, "running");
          addLog("info", `${label} is running…`);
        }
        if (event.event === "block_progress") {
          setLogs(previous => {
            const message = `${label}: ${event.message}`;
            if (previous.at(-1)?.message === message) return previous;
            return [...previous.slice(-299), { kind: "info", message }];
          });
        }
        if (event.event === "block_done") {
          setResults(previous => ({ ...previous, [event.blockId]: event }));
          setResultId(event.blockId);
          setStatus(event.blockId, "done");
          addLog(event.failed_rows ? "error" : "success", `${label}: ${event.row_count} rows${event.failed_rows ? `, ${event.failed_rows} failed (inspect results)` : ""}`);
        }
        if (event.event === "error") {
          if (event.blockId) setStatus(event.blockId, "error");
          addLog("error", event.message || "Workflow failed.");
          finished = true;
        }
        if (event.event === "complete") {
          addLog(
            "success",
            `Workflow ${event.status === "partial" ? "finished with row failures" : "complete"}. ${event.row_count} rows. ${event.downloads?.length || 0} downloadable files.`,
          );
          finished = true;
          setPanelTab("results");
        }
      };
      while (!finished) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        lines.forEach(processLine);
        if (done) {
          processLine(buffer);
          break;
        }
      }
      await reader.cancel();
      if (!finished)
        throw new Error(
          "The execution stream ended unexpectedly. Please try again.",
        );
    } catch (error) {
      addLog(
        "error",
        error instanceof Error && error.name === "AbortError" ? "Stopped local execution. Already submitted Sixtyfour jobs may still complete and incur charges." : error instanceof Error ? error.message : "Could not reach the backend.",
      );
    } finally {
      setRunning(false);
      controller.current = null;
      setNodes((previous) =>
        previous.map((node) =>
          node.data.status === "pending" || node.data.status === "running"
            ? { ...node, data: { ...node.data, status: "idle" } }
            : node,
        ),
      );
    }
  };
  const starter = (sourceType = "read_csv") => {
    const first = crypto.randomUUID(),
      last = crypto.randomUUID();
    setNodes([
      {
        id: first,
        type: "block",
        position: { x: 80, y: 150 },
        data: {
          blockType: sourceType,
          config: defaultConfig(catalog.find(item => item.type === sourceType)!),
          status: "idle",
        },
      },
      {
        id: last,
        type: "block",
        position: { x: 430, y: 150 },
        data: { blockType: "save_csv", config: { filename: "results.csv" }, status: "idle" },
      },
    ]);
    setEdges([{ id: crypto.randomUUID(), source: first, target: last }]);
    setSelectedId(first);
    requestAnimationFrame(() => fitView({ padding: 0.3, maxZoom: 1 }));
  };
  const activeResult = results[resultId];
  const SelectedIcon = icons[selected?.data.blockType as keyof typeof icons] || Workflow;
  return (
    <CatalogContext.Provider value={catalog}>
    <div className="workspace">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <Workflow size={22} />
          </span>
          sixtyfour
          <span className="brand-divider" />
          <span className="workspace-label">Workflow studio</span>
        </div>
        <span className="api-status">{catalog.length ? (apiConfigured ? "API key configured" : "Set SIXTYFOUR_API_KEY in backend/.env") : "Connecting to backend…"}</span>
      </header>
      <div className="workspace-toolbar">
        <div>
          <h1>
            Workflow
          </h1>
        </div>
        <div className="toolbar-actions">
          {running && <button className="secondary-button" onClick={() => controller.current?.abort()}>Stop</button>}
          <span className="block-count">
            {nodes.length} blocks · {edges.length} connections
          </span>
          <button
            className="primary-button"
            disabled={running || uploading || !nodes.length || !catalog.length}
            onClick={runFlow}
          >
            {running ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Play size={15} fill="currentColor" />
            )}
            {running ? "Running workflow" : "Run workflow"}
          </button>
        </div>
      </div>
      <div className="editor-layout">
        <aside className="catalog">
          <div className="section-heading">
            <h2>Blocks</h2>
            <span>{catalog.length}</span>
          </div>
          <label className="search">
            <Search size={15} />
            <input
              aria-label="Search blocks"
              placeholder="Search blocks…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="catalog-groups">
            {catalogError && <div className="config-error">Could not load blocks. Start the backend and reload.<br />{catalogError}</div>}
            {["Data", "Search", "Enrichment", "Logic"].map((group) => (
              <section key={group}>
                <h3>{group}</h3>
                {catalog
                  .filter(
                    (item) =>
                      item.group === group &&
                      item.label
                        .toLowerCase()
                        .includes(query.toLowerCase()),
                  )
                  .map((item) => {
                    const Icon = icons[item.type as keyof typeof icons] || Workflow;
                    return (
                      <button
                        key={item.type}
                        className="catalog-block"
                        disabled={running}
                        draggable={!running}
                        onDragStart={(event) =>
                          event.dataTransfer.setData(
                            "application/block-type",
                            item.type,
                          )
                        }
                        onClick={() => addBlock(item.type)}
                      >
                        <span className={`icon-tile purple`}>
                          <Icon size={18} />
                        </span>
                        <span>
                          <strong>{item.label}</strong>
                        </span>
                        <Plus size={14} className="add-icon" />
                      </button>
                    );
                  })}
              </section>
            ))}
          </div>
        </aside>
        <main className="canvas-column">
          <div
            className="canvas"
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
            }}
            onDrop={(event) => {
              event.preventDefault();
              const type = event.dataTransfer.getData("application/block-type");
              if (catalog.some((item) => item.type === type))
                addBlock(
                  type,
                  screenToFlowPosition({ x: event.clientX, y: event.clientY }),
                );
            }}
          >
            <ReactFlow
              nodes={nodes}
              edges={edges.map(edge => ({ ...edge, data: { ...edge.data, locked: running } }))}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              defaultEdgeOptions={edgeOptions}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              isValidConnection={(connection) =>
                !running && canConnect(connection, edges)
              }
              onNodeClick={(_, node) => { setSelectedId(node.id); if (results[node.id]) { setResultId(node.id); setPanelTab("results"); } }}
              onPaneClick={() => setSelectedId(null)}
              nodesDraggable={!running}
              nodesConnectable={!running}
              deleteKeyCode={running ? null : ["Backspace", "Delete"]}
              minZoom={0.25}
              maxZoom={1}
              defaultViewport={{ x: 0, y: 0, zoom: 1 }}
              fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
              connectOnClick
              connectionRadius={30}
            >
              <Background color="#303030" gap={22} size={1} />
              <Controls showInteractive={false} fitViewOptions={{ maxZoom: 1, padding: 0.2 }} />
              <MiniMap
                pannable
                zoomable
                nodeColor="#777777"
                maskColor="rgba(16,16,16,.7)"
              />
            </ReactFlow>
            {!nodes.length && (
              <div className="empty-state">
                <button className="primary-button" disabled={!catalog.length} onClick={() => {
                  try { starter(); }
                  catch (error) { addLog("error", error instanceof Error ? error.message : "Could not create workflow"); }
                }}>
                  Add CSV workflow <ArrowRight size={16} />
                </button>
                <button className="secondary-button" disabled={!catalog.length} onClick={() => starter("find_prospects")}>Start from a search</button>
              </div>
            )}
          </div>
          <section className={`execution-panel ${showLogs ? "" : "collapsed"}`}>
            <div className="execution-header">
              <div className="panel-tabs">
                <button className={panelTab === "logs" ? "active" : ""} onClick={() => { setPanelTab("logs"); setShowLogs(true); }}><Terminal size={14} /> Execution log</button>
                <button className={panelTab === "results" ? "active" : ""} onClick={() => { setPanelTab("results"); setShowLogs(true); }}><Table2 size={14} /> Results</button>
                {running && <span className="execution-badge active">Running</span>}
              </div>
              <button className="icon-button" aria-label={showLogs ? "Collapse results" : "Expand results"} onClick={() => setShowLogs(!showLogs)}>{showLogs ? "−" : "+"}</button>
            </div>
            {showLogs && (panelTab === "logs" ? <div className="logs" role="log" aria-live="polite">
              {logs.length ? logs.map((log, index) => <div className={`log-line ${log.kind}`} key={index}>
                {log.kind === "error" ? <AlertCircle size={14} /> : log.kind === "success" ? <Check size={14} /> : <span className="log-dot" />}
                <span>{log.message}</span>
              </div>) : <div className="log-placeholder">No runs yet.</div>}
            </div> : <div className="results-content">
              <div className="results-toolbar">
                <select aria-label="Result step" className="text-input" value={resultId} onChange={event => setResultId(event.target.value)}>
                  <option value="">Choose a completed step</option>
                  {Object.keys(results).map((id, index) => <option key={id} value={id}>{index + 1}. {catalog.find(item => item.type === nodes.find(node => node.id === id)?.data.blockType)?.label || "Previous block"}</option>)}
                </select>
                {activeResult && <span>{activeResult.row_count} rows · showing up to 20{activeResult.failed_rows ? ` · ${activeResult.failed_rows} failed` : ""}</span>}
                {activeResult?.downloads.map(file => <a key={file.url} className="download-link" href={file.url} download><Download size={14} />{file.filename}</a>)}
              </div>
              {activeResult ? <>
                <div className="result-table-wrap"><table className="result-table"><thead><tr>{activeResult.columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
                  <tbody>{activeResult.preview.map((row, index) => <tr key={index}>{activeResult.columns.map(column => {
                    const value = row[column];
                    const text = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
                    return <td key={column} title={text}>{text ? <button className="cell-value" onClick={() => {
                      let value = text;
                      try { value = JSON.stringify(JSON.parse(text), null, 2); } catch { /* Plain text cell. */ }
                      setCellDetail({ column, value });
                    }}>{text}</button> : <span className="null-value">—</span>}</td>;
                  })}</tr>)}</tbody></table></div>
                {!activeResult.row_count && <p className="field-help">This step returned no rows.</p>}
                {cellDetail && <div className="cell-detail"><div><strong>{cellDetail.column}</strong><button className="icon-button" aria-label="Close cell details" onClick={() => setCellDetail(null)}><X size={14} /></button></div><pre>{cellDetail.value}</pre></div>}
                {Object.keys(activeResult.metadata || {}).length > 0 && <details className="result-metadata"><summary>Column statistics / API metadata</summary><pre>{JSON.stringify(activeResult.metadata, null, 2)}</pre></details>}
              </> : <p className="field-help">Run a workflow to inspect each step’s output. Add Save CSV for a full download.</p>}
            </div>)}
          </section>
        </main>
        {selected && selectedMeta && (
          <ResizableInspector>
            <div className="section-heading">
              <h2>Block settings</h2>
              <button
                className="icon-button"
                aria-label="Close settings"
                onClick={() => setSelectedId(null)}
              >
                <X size={17} />
              </button>
            </div>
            <div className="inspector-title">
              <span className={`icon-tile purple`}>
                <SelectedIcon size={22} />
              </span>
              <h2>{selectedMeta.label}</h2>
            </div>
            <div className="inspector-divider" />
            <fieldset disabled={running}>
              <BlockSettings definition={selectedMeta} config={selected.data.config} patch={patchConfig} earlierBlocks={earlierBlocks} upload={upload} uploading={uploading} />
            </fieldset>
            <button
              className="delete-button"
              disabled={running}
              onClick={() => {
                setNodes((previous) =>
                  previous.filter((node) => node.id !== selected.id),
                );
                setEdges((previous) =>
                  previous.filter(
                    (edge) =>
                      edge.source !== selected.id &&
                      edge.target !== selected.id,
                  ),
                );
                setSelectedId(null);
              }}
            >
              <Trash2 size={15} />
              Delete block
            </button>
          </ResizableInspector>
        )}
      </div>
    </div>
    </CatalogContext.Provider>
  );
}
export function BlockEditor() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}
