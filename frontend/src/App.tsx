import { BlockEditor } from "./components/BlockEditor";
import { useState } from "react";
import { AgentChat } from "./agent/AgentChat";
import "./agent/agent.css";

function App() {
  const [mode, setMode] = useState<"manual" | "agent">("manual");
  const toggle = <div className="mode-switch" role="group" aria-label="Workspace mode">
    {(["manual", "agent"] as const).map(value => <button key={value} aria-pressed={mode === value}
      onClick={() => setMode(value)}>{value}</button>)}
  </div>;
  // Keep both workspaces mounted: switching does not lose the canvas or stop a run.
  return <>
    <div hidden={mode !== "manual"}><BlockEditor modeSwitch={toggle} /></div>
    <div hidden={mode !== "agent"}><AgentChat modeSwitch={toggle} /></div>
  </>;
}

export default App;
