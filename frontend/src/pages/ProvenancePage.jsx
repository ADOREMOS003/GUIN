import { useEffect, useMemo, useState } from "react";
import ReactFlow, { Background, Controls } from "react-flow-renderer";

import { api } from "../api/client";

export function ProvenancePage() {
  const [records, setRecords] = useState([]);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [diff, setDiff] = useState(null);

  useEffect(() => {
    api.provenance().then((d) => setRecords(d.records || [])).catch(console.error);
  }, []);

  const nodes = useMemo(() => {
    if (!diff?.diff?.workflow_graph_difference) return [];
    const added = diff.diff.workflow_graph_difference.added_nodes || [];
    const removed = diff.diff.workflow_graph_difference.removed_nodes || [];
    return [
      ...added.map((n, i) => ({
        id: `a-${n}`,
        data: { label: `+ ${n}` },
        position: { x: 50 + i * 180, y: 80 },
        style: { background: "#134e4a", color: "#d1fae5" },
      })),
      ...removed.map((n, i) => ({
        id: `r-${n}`,
        data: { label: `- ${n}` },
        position: { x: 50 + i * 180, y: 220 },
        style: { background: "#7f1d1d", color: "#fecaca" },
      })),
    ];
  }, [diff]);

  const onCompare = async () => setDiff(await api.provenanceDiff(a, b));
  const onReplay = async () => {
    if (!a) return;
    await api.replay(a);
    alert("Replay started/completed.");
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Provenance Viewer</h1>
      <div className="grid gap-3 md:grid-cols-2">
        <select value={a} onChange={(e) => setA(e.target.value)} className="rounded bg-slate-950 p-2 text-sm">
          <option value="">Select record A</option>
          {records.map((r) => (
            <option key={`a-${r}`} value={r}>{r}</option>
          ))}
        </select>
        <select value={b} onChange={(e) => setB(e.target.value)} className="rounded bg-slate-950 p-2 text-sm">
          <option value="">Select record B</option>
          {records.map((r) => (
            <option key={`b-${r}`} value={r}>{r}</option>
          ))}
        </select>
      </div>
      <div className="flex gap-2">
        <button className="rounded bg-guin-accent px-4 py-2 text-slate-900" onClick={onCompare}>Compare</button>
        <button className="rounded border border-guin-muted px-4 py-2" onClick={onReplay}>Replay A</button>
      </div>
      {diff && (
        <>
          <pre className="max-h-64 overflow-auto rounded bg-slate-950 p-3 text-xs">{diff.markdown}</pre>
          <div className="h-80 rounded border border-guin-muted bg-slate-900/40">
            <ReactFlow nodes={nodes} edges={[]} fitView>
              <Background />
              <Controls />
            </ReactFlow>
          </div>
        </>
      )}
    </div>
  );
}
