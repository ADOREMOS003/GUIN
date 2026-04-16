import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";

export function DashboardPage() {
  const [status, setStatus] = useState(null);
  const [instruction, setInstruction] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.status().then(setStatus).catch(console.error);
  }, []);

  const onQuickRun = async () => {
    navigate("/run", { state: { instruction } });
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <section className="rounded border border-guin-muted bg-slate-900/50 p-4">
        <h2 className="mb-3 text-lg font-medium">System Status</h2>
        {status ? (
          <div className="grid gap-2 text-sm">
            <div>Python: {status.python_version}</div>
            <div>Container runtime: {status.container_runtime}</div>
            <div>Available containers: {status.available_containers.length}</div>
          </div>
        ) : (
          <div className="text-sm text-slate-400">Loading status...</div>
        )}
      </section>
      <section className="rounded border border-guin-muted bg-slate-900/50 p-4">
        <h2 className="mb-3 text-lg font-medium">Recent Workflow Runs</h2>
        <div className="space-y-2 text-sm">
          {(status?.recent_runs || []).map((r) => (
            <div key={r.run_id} className="flex items-center justify-between rounded bg-slate-800/60 px-3 py-2">
              <div className="truncate pr-4">{r.instruction}</div>
              <span
                className={`rounded px-2 py-0.5 text-xs ${
                  r.status === "success"
                    ? "bg-emerald-500/20 text-emerald-300"
                    : r.status === "failed"
                      ? "bg-red-500/20 text-red-300"
                      : "bg-amber-500/20 text-amber-300"
                }`}
              >
                {r.status}
              </span>
            </div>
          ))}
        </div>
      </section>
      <section className="rounded border border-guin-muted bg-slate-900/50 p-4">
        <h2 className="mb-2 text-lg font-medium">Quick Start</h2>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="Enter a natural language instruction..."
          className="h-28 w-full rounded border border-guin-muted bg-slate-950 p-3 text-sm"
        />
        <button onClick={onQuickRun} className="mt-3 rounded bg-guin-accent px-4 py-2 font-medium text-slate-900">
          Run
        </button>
      </section>
    </div>
  );
}
