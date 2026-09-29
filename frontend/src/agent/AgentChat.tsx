import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowUp, Check, Download, FileSpreadsheet, LoaderCircle, Paperclip, Plus, Sparkles, Square, Workflow, X } from "lucide-react";
import { errorMessage, streamAgent, type AgentConfig, type AgentEvent, type Budgets } from "./api";

type Entry = { key: string; kind: "user" | "message" | "plan" | "tool" | "result"; event: AgentEvent; progress?: string };
type Attachment = { path: string; name: string };
const label = (value: string) => value.replaceAll("_", " ");
const cell = (value: unknown) => value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);

function Preview({ event }: { event: AgentEvent }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const table = tableRef.current;
    if (!table) return;
    // Horizontal table scrolling must not trap vertical conversation scrolling.
    // Retain independent vertical scrolling for tall intermediate tool previews.
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.shiftKey || !event.deltaY || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
      const atEdge = event.deltaY > 0
        ? table.scrollTop + table.clientHeight >= table.scrollHeight - 1
        : table.scrollTop <= 1;
      if (!atEdge) return;
      const conversation = table.closest<HTMLElement>(".agent-conversation");
      if (!conversation) return;
      const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? conversation.clientHeight : 1;
      event.preventDefault();
      conversation.scrollBy({ top: event.deltaY * scale, behavior: "instant" });
    };
    table.addEventListener("wheel", onWheel, { passive: false });
    return () => table.removeEventListener("wheel", onWheel);
  }, [event.columns]);
  return <>
    {!!event.columns?.length && <div ref={tableRef} className="agent-table-wrap"><table className="agent-table">
      <thead><tr>{event.columns.map(name => <th key={name}>{name}</th>)}</tr></thead>
      <tbody>{event.preview?.map((row, i) => <tr key={i}>{event.columns!.map(name => <td key={name}>
        <button onClick={() => setExpanded(cell(row[name]))} title="View full preview value">{cell(row[name])}</button>
      </td>)}</tr>)}</tbody>
    </table>{!event.preview?.length && <p className="agent-muted">No matching rows.</p>}</div>}
    {expanded !== null && <div className="agent-modal-backdrop" onClick={() => setExpanded(null)}>
      <section className="agent-cell-modal" role="dialog" aria-modal="true" aria-label="Cell value" onClick={e => e.stopPropagation()}>
        <button autoFocus className="icon-button" aria-label="Close cell value" onClick={() => setExpanded(null)}><X size={18} /></button>
        <pre>{expanded}</pre>
      </section>
    </div>}
  </>;
}

function ToolCard({ entry, running }: { entry: Entry; running: boolean }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return <div className="agent-tool-card">
    <button type="button" className="agent-tool-summary" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(value => !value)}><span className="agent-tool-icon">{entry.event.event === "tool_started" && running ? <LoaderCircle size={16} className="spin" /> : entry.event.event === "tool_done" ? <Check size={16} /> : <Square size={14} />}</span>
      <span><strong>{label(entry.event.tool || "Tool")}</strong><small>{entry.progress || (entry.event.event === "tool_done" ? `${entry.event.row_count} rows · ${entry.event.failed_rows || 0} failed` : entry.event.message)}</small></span>
      <span className="agent-tool-status">{entry.event.event === "tool_done" ? "done" : entry.event.event === "tool_error" ? "stopped" : running ? "running" : "interrupted"}</span></button>
    {open && <div id={panelId} className="agent-tool-details"><p>{entry.event.message}</p><pre>{JSON.stringify(entry.event.arguments, null, 2)}</pre><Preview event={entry.event} /></div>}
  </div>;
}

export function AgentChat({ modeSwitch }: { modeSwitch: ReactNode }) {
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [setupError, setSetupError] = useState("");
  const [query, setQuery] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [uploading, setUploading] = useState(false);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [useJev, setUseJev] = useState(false);
  const [activity, setActivity] = useState("");
  const [error, setError] = useState("");
  const [budgets, setBudgets] = useState<Budgets | null>(null);
  const [recoverable, setRecoverable] = useState(false);
  const runId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);

  async function loadConfig() {
    try {
      const response = await fetch("/api/agent/config");
      if (!response.ok) throw new Error(await errorMessage(response));
      setConfig(await response.json()); setSetupError("");
    } catch (err) { setSetupError(err instanceof Error ? err.message : "Cannot connect to the backend."); }
  }
  useEffect(() => { void loadConfig(); return () => controller.current?.abort(); }, []);
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [entries.length, running]);

  function receive(event: AgentEvent) {
    if (event.use_jev !== undefined) setUseJev(event.use_jev);
    if (event.budgets) setBudgets(event.budgets);
    if (event.event === "run_started") { runId.current = event.run_id!; return; }
    if (event.event === "heartbeat") return;
    if (event.event === "thinking") { setActivity(event.message || "Planning…"); return; }
    if (event.event === "block_progress") {
      setActivity(event.message || "Working…");
      setEntries(previous => previous.map(entry => entry.kind === "tool" && entry.event.id === event.blockId
        ? { ...entry, progress: event.message } : entry));
      return;
    }
    if (event.event === "tool_done" || event.event === "tool_error") {
      setEntries(previous => previous.map(entry => entry.kind === "tool" && entry.event.id === event.id
        ? { ...entry, event: { ...entry.event, ...event }, progress: undefined } : entry));
      return;
    }
    if (event.event === "complete") {
      setWaiting(event.status === "needs_input");
      setActivity(""); setRecoverable(false);
      setEntries(previous => [...previous.map(entry => entry.kind === "tool" && entry.event.event === "tool_started"
        ? { ...entry, event: { ...entry.event, event: "tool_error", message: "Stopped before this tool completed." } } : entry),
        { key: crypto.randomUUID(), kind: "result", event }]);
      return;
    }
    setEntries(previous => [...previous, { key: crypto.randomUUID(),
      kind: event.event === "plan" ? "plan" : event.event === "tool_started" ? "tool" : "message", event }]);
    if (event.event === "tool_started") setActivity(event.message || "Running tool…");
  }

  async function upload(file: File) {
    if (!file.name.toLowerCase().endsWith(".csv")) { setError("Choose a CSV file."); return; }
    if (!file.size || file.size > 10 * 1024 * 1024) { setError("Choose a nonempty CSV up to 10 MB."); return; }
    setUploading(true); setError("");
    try {
      const response = await fetch("/api/uploads", { method: "POST", headers: { "Content-Type": "text/csv" }, body: file });
      if (!response.ok) throw new Error(await errorMessage(response));
      setAttachment({ path: (await response.json()).path, name: file.name });
    } catch (err) { setError(err instanceof Error ? err.message : "Upload failed."); }
    finally { setUploading(false); }
  }

  async function send() {
    if (!query.trim() || running || uploading || !config?.configured) return;
    const text = query.trim();
    const resumedId = waiting ? runId.current : null;
    if (!resumedId) { runId.current = null; setBudgets(null); }
    setEntries(previous => [...previous, { key: crypto.randomUUID(), kind: "user",
      event: { event: "user", message: text + (attachment ? `\nAttached: ${attachment.name}` : "") } }]);
    const uploaded = attachment;
    setQuery(""); setAttachment(null); setError(""); setRunning(true); setStopping(false); setWaiting(false); setRecoverable(false);
    setActivity("Starting…");
    controller.current = new AbortController();
    try {
      await streamAgent({ query: text, ...(resumedId ? { run_id: resumedId } : { use_jev: useJev }),
        ...(uploaded ? { upload_path: uploaded.path } : {}) }, controller.current.signal, receive);
    } catch (err) {
      const aborted = err instanceof DOMException && err.name === "AbortError";
      setError(aborted ? "Run stopped." : err instanceof Error ? err.message : "Run failed.");
      setRecoverable(Boolean(runId.current));
      if (runId.current) void fetch(`/api/agent/runs/${runId.current}/cancel`, { method: "POST" });
      else { setQuery(text); setAttachment(uploaded); }
    } finally { setRunning(false); setStopping(false); setActivity(""); }
  }

  async function stop() {
    setStopping(true);
    if (!runId.current) { controller.current?.abort(); return; }
    try {
      const response = await fetch(`/api/agent/runs/${runId.current}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(await errorMessage(response));
      setActivity("Stopping and saving available results…");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not stop the run."); setStopping(false); }
  }

  async function recover() {
    try {
      const response = await fetch(`/api/agent/runs/${runId.current}`);
      if (!response.ok) throw new Error(await errorMessage(response));
      const event = await response.json();
      if (event.event === "complete") { receive(event); setError(""); }
      else setError("The run is still stopping. Try recovering its result again shortly.");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not recover the result."); }
  }

  function newChat() {
    if (waiting && runId.current) void fetch(`/api/agent/runs/${runId.current}/cancel`, { method: "POST" });
    runId.current = null; setEntries([]); setWaiting(false); setBudgets(null); setError("");
    setRecoverable(false); setQuery(""); setAttachment(null);
  }

  return <div className="workspace agent-workspace">
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Workflow size={22} /></span>sixtyfour
        <span className="brand-divider" /><span className="workspace-label">Workflow studio</span></div>
      {modeSwitch}<span className="api-status">{config?.configured ? `Planner · ${config.model}` : "Agent workspace"}</span>
    </header>
    <div className="agent-toolbar"><span><span className={`agent-dot ${running ? "active" : ""}`} />{running ? "Agent working" : waiting ? "Waiting for your answer" : "Agent"}</span>
      <div className="agent-toolbar-actions">
        <button type="button" role="switch" aria-checked={useJev} aria-label="Jev" className="agent-jev-toggle"
          disabled={running || waiting || uploading || !config?.jev?.configured}
          title={!config?.jev?.configured ? "Set TYPESAFE_API_KEY on the server."
            : running || waiting ? "Jev is fixed for this run. Start a new chat to change it." : "Use Jev to choose eligible next tool calls."}
          onClick={() => setUseJev(value => !value)}>
          <span className="agent-jev-track" aria-hidden="true"><span /></span>Jev <span>{useJev ? "On" : "Off"}</span>
        </button>
        <button className="secondary-button" disabled={running || uploading} onClick={newChat}><Plus size={15} /> New chat</button>
      </div></div>
    <main className={`agent-conversation${entries.length ? "" : " agent-conversation-empty"}`}>
      {!entries.length && <div className="agent-welcome">
        <div className="agent-examples">{[
          ["Find companies", "Find 25 US B2B SaaS companies with 50–200 employees."],
          ["Find decision makers", "Find heads of engineering at 10 climate tech companies and their work emails."],
          ["Clean and enrich a CSV", "Remove duplicate emails from my CSV, then find work emails for leads missing them."],
        ].map(([title, text]) => <button key={title} onClick={() => setQuery(text)}><span>{title}</span><p>{text}</p><ArrowUp size={15} /></button>)}</div>
      </div>}
      <div className="agent-transcript">{entries.map(entry => <div key={entry.key} className={`agent-entry agent-${entry.kind}`}>
        {entry.kind === "user" ? <p>{entry.event.message}</p> : entry.kind === "tool" ? <ToolCard entry={entry} running={running} /> : entry.kind === "plan" ? <div className="agent-plan-card"><div className="agent-assistant-label"><Sparkles size={15} /> Plan</div>
          <p>{entry.event.message}</p><ol>{entry.event.steps?.map((step, i) => <li key={i}>{step}</li>)}</ol>
          {entry.event.contract && <p className="agent-contract"><strong>Done when:</strong> {entry.event.contract.description}</p>}
        </div> : entry.kind === "result" ? <section className={`agent-result-card ${entry.event.status}`}>
          <div className="agent-assistant-label"><Sparkles size={15} />{label(entry.event.status || "Result")}</div>
          <p>{entry.event.message}</p>
          {entry.event.status !== "needs_input" && !!entry.event.checks?.issues.length && <ul className="agent-result-issues">{entry.event.checks.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>}
          {!!entry.event.columns?.length && <><div className="agent-result-heading"><strong>{entry.event.row_count} rows</strong><span>Preview · first {entry.event.preview?.length || 0} rows</span></div><Preview event={entry.event} /></>}
          <div className="agent-downloads">{entry.event.downloads?.map(file => <a key={file.url} href={file.url} download><Download size={15} /> Download CSV</a>)}</div>
          {entry.event.export_error && <p className="agent-error">{entry.event.export_error}</p>}
        </section> : <p className="agent-notice">{entry.event.message}</p>}
      </div>)}<div ref={chatEnd} /></div>
    </main>
    <div className="agent-composer-area">
      {running && <div className="agent-live" role="status"><LoaderCircle size={14} className="spin" /><span>{activity}</span></div>}
      {(setupError || config?.configured === false) && <div className="agent-setup">{setupError || "Set OPENAI_API_KEY and AGENT_MODEL in backend/.env to enable the agent. Your manual workflows are ready to use."}<button onClick={() => void loadConfig()}>Check again</button></div>}
      {error && <div className="agent-error" role="alert">{error} {recoverable && <button onClick={() => void recover()}>Recover result</button>}</div>}
      <form className="agent-composer" onSubmit={e => { e.preventDefault(); void send(); }}>
        {attachment && <div className="agent-attachment"><FileSpreadsheet size={15} />{attachment.name}<button type="button" aria-label="Remove CSV" onClick={() => setAttachment(null)}><X size={14} /></button></div>}
        <textarea aria-label={waiting ? "Answer the agent’s question" : "Describe the dataset you want to build"}
          placeholder={waiting ? "Answer the agent’s question to continue…" : "Describe the leads or companies you want to find, enrich, or qualify…"}
          value={query} onChange={e => setQuery(e.target.value)} disabled={running} maxLength={12000}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
        <div className="agent-composer-actions"><input ref={fileInput} type="file" accept=".csv,text/csv" hidden onChange={e => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }} />
          <button type="button" className="agent-attach-button" disabled={running || uploading || waiting} onClick={() => fileInput.current?.click()}><Paperclip size={16} />{uploading ? "Uploading…" : "Attach CSV"}</button>
          {running ? <button type="button" className="agent-send-button stop" disabled={stopping} onClick={() => void stop()}><Square size={13} fill="currentColor" />{stopping ? "Stopping" : "Stop"}</button>
            : <button type="submit" className="agent-send-button" disabled={!query.trim() || uploading || !config?.configured} aria-label={waiting ? "Send answer" : "Build dataset"}><ArrowUp size={18} /></button>}
        </div>
      </form>
      {budgets && config && <div className="agent-footer"><span>{`${budgets.tool_attempts}/${config.limits.tool_attempts} tool attempts · ${budgets.model_requests}/${config.limits.model_requests} model requests`}
        {budgets.jev_requests !== undefined && <span className="agent-jev-stats">{` · ${budgets.planner_requests} planner / ${budgets.jev_requests} Jev · ${budgets.jev_selections} Jev selections · ${budgets.jev_fallbacks} fallbacks`}</span>}</span>
        <details className="agent-limits"><summary>Stopping rules</summary><div>
          <strong>Bounded steps. No global time limit.</strong>
          <p>Stops when the completion criteria are met or the task cannot proceed.</p>
          <ul><li>At most {config?.limits.tool_attempts ?? 20} tool attempts and {config?.limits.model_requests ?? 30} model requests.</li>
            <li>{config?.limits.invalid_decisions ?? 2} consecutive invalid decisions or {config?.limits.tool_failures ?? 2} consecutive tool failures.</li>
            <li>{config?.limits.no_progress ?? 3} consecutive tool executions without new progress.</li>
            <li>Identical actions on unchanged data are blocked.</li><li>Sixtyfour access or credit errors stop immediately.</li>
            {useJev && <li>Jev shares the model request budget. Uncertain or unavailable routing falls back to the planner.</li>}
            <li>Clarification pauses the run and preserves its budgets.</li><li>Waiting for an active job does not consume iterations.</li>
            <li>Stop prevents further work; already submitted remote jobs may still finish.</li></ul>
        </div></details>
      </div>}
    </div>
  </div>;
}
