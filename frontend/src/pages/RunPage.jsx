import Editor from "@monaco-editor/react";
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

import { api } from "../api/client";

function buildWsUrl(runId) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/v1/ws/logs?run_id=${encodeURIComponent(runId)}`;
}

export function RunPage() {
  const { state } = useLocation();
  const [instruction, setInstruction] = useState(state?.instruction || "");
  const [bidsDir, setBidsDir] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [model, setModel] = useState("claude-sonnet-4-20250514");
  const [dryRun, setDryRun] = useState(true);
  const [runData, setRunData] = useState(null);
  const [logs, setLogs] = useState([]);

  const wsUrl = useMemo(() => (runData?.run?.run_id ? buildWsUrl(runData.run.run_id) : null), [runData]);

  useEffect(() => {
    if (!wsUrl || dryRun) return;
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg?.data?.type === "snapshot") setLogs(msg.data.logs || []);
      if (msg?.data?.type === "log") setLogs((prev) => [...prev, msg.data.line]);
    };
    return () => ws.close();
  }, [wsUrl, dryRun]);

  const onRun = async () => {
    const res = await api.run({
      instruction,
      bids_dir: bidsDir,
      output_dir: outputDir,
      model,
      dry_run: dryRun,
    });
    setRunData(res);
    setLogs([]);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Workflow Runner</h1>
      <textarea
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        className="h-36 w-full rounded border border-guin-muted bg-slate-950 p-3 text-sm"
        placeholder="run fmriprep on sub-01 with 6mm smoothing"
      />
      <div className="grid gap-3 md:grid-cols-2">
        <input
          value={bidsDir}
          onChange={(e) => setBidsDir(e.target.value)}
          className="rounded border border-guin-muted bg-slate-950 p-2 text-sm"
          placeholder="BIDS directory"
        />
        <input
          value={outputDir}
          onChange={(e) => setOutputDir(e.target.value)}
          className="rounded border border-guin-muted bg-slate-950 p-2 text-sm"
          placeholder="Output directory"
        />
      </div>
      <div className="flex items-center gap-4">
        <select value={model} onChange={(e) => setModel(e.target.value)} className="rounded bg-slate-950 p-2 text-sm">
          <option>claude-sonnet-4-20250514</option>
          <option>claude-3-5-haiku-20241022</option>
        </select>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          Dry run
        </label>
        <button className="rounded bg-guin-accent px-4 py-2 font-medium text-slate-900" onClick={onRun}>
          Run
        </button>
      </div>

      {runData && (
        <div className="space-y-3 rounded border border-guin-muted bg-slate-900/50 p-4">
          <h2 className="text-lg font-medium">Results</h2>
          <Editor
            height="280px"
            language="python"
            value={runData.generated_code}
            theme="vs-dark"
            options={{ readOnly: true, minimap: { enabled: false } }}
          />
          {!dryRun && (
            <pre className="max-h-60 overflow-auto rounded bg-slate-950 p-3 text-xs">
              {logs.length ? logs.join("\n") : "Waiting for logs..."}
            </pre>
          )}
          {runData.run?.provenance_file && (
            <div className="text-sm">
              Provenance: <code>{runData.run.provenance_file}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
