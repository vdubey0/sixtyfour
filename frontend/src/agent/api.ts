export type Budgets = {
  tool_attempts: number; model_requests: number; tools_remaining: number; models_remaining: number;
  consecutive_invalid: number; consecutive_failures: number; consecutive_no_progress: number;
  jev_requests?: number; planner_requests?: number; jev_selections?: number; jev_fallbacks?: number;
  jev_latency_ms?: number; jev_input_tokens?: number; jev_output_tokens?: number; jev_cost?: number | null;
};
export type Limits = {
  tool_attempts: number; model_requests: number; invalid_decisions: number;
  tool_failures: number; no_progress: number; model_timeout: number; max_rows: number;
};
export type AgentConfig = { configured: boolean; model: string | null; limits: Limits;
  jev?: { configured: boolean; model: string } };
export type AgentEvent = {
  event: string; id?: string; blockId?: string; run_id?: string; tool?: string; message?: string;
  status?: string; reason?: string; steps?: string[]; contract?: { description: string };
  use_jev?: boolean; confidence?: number; probability?: number; threshold?: number; latency_ms?: number;
  arguments?: Record<string, unknown>; budgets?: Budgets; limits?: Limits;
  row_count?: number; columns?: string[]; preview?: Record<string, unknown>[];
  failed_rows?: number; checks?: { satisfied: boolean; issues: string[] };
  downloads?: { filename: string; url: string }[]; export_error?: string | null;
};

export async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`;
  } catch { return `Request failed (${response.status}).`; }
}

export async function streamAgent(payload: { query: string; upload_path?: string; run_id?: string; use_jev?: boolean },
  signal: AbortSignal, receive: (event: AgentEvent) => void) {
  const response = await fetch("/api/agent/runs", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  if (!response.body) throw new Error("The server did not open a response stream.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", complete = false;
  const consume = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as AgentEvent;
    if (event.event === "complete") complete = true;
    receive(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.forEach(consume);
      if (done) break;
    }
    consume(buffer);
    if (!complete) throw new Error("Connection ended before a final result. The run was stopped; recover its saved result below.");
  } finally { reader.releaseLock(); }
}
