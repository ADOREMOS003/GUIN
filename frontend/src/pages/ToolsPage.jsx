import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";

export function ToolsPage() {
  const [tools, setTools] = useState([]);
  const [q, setQ] = useState("");
  const [activeTool, setActiveTool] = useState(null);
  const [formData, setFormData] = useState({});
  const [runResult, setRunResult] = useState(null);

  useEffect(() => {
    api.tools().then((d) => setTools(d.tools || [])).catch(console.error);
  }, []);

  const filtered = useMemo(
    () => tools.filter((t) => `${t.name} ${t.description}`.toLowerCase().includes(q.toLowerCase())),
    [tools, q],
  );

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Tool Registry</h1>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search tools..."
        className="w-full rounded border border-guin-muted bg-slate-950 p-2 text-sm"
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((tool) => (
          <article key={tool.name} className="rounded border border-guin-muted bg-slate-900/60 p-4">
            <h2 className="font-medium text-guin-accent">{tool.name}</h2>
            <p className="mt-1 text-sm text-slate-300">{tool.description || "No description."}</p>
            <details className="mt-3">
              <summary className="cursor-pointer text-sm">Input schema</summary>
              <pre className="mt-2 overflow-auto rounded bg-slate-950 p-2 text-xs">
                {JSON.stringify(tool.input_schema, null, 2)}
              </pre>
            </details>
            <button
              className="mt-3 rounded border border-guin-muted px-3 py-1 text-sm"
              onClick={() => {
                setActiveTool(tool);
                setFormData({});
                setRunResult(null);
              }}
            >
              Try it
            </button>
          </article>
        ))}
      </div>

      {activeTool && (
        <section className="rounded border border-guin-muted bg-slate-900/50 p-4">
          <h2 className="mb-2 text-lg font-medium">Try: {activeTool.name}</h2>
          <div className="grid gap-2 md:grid-cols-2">
            {Object.entries(activeTool.input_schema?.properties || {}).map(([k]) => (
              <input
                key={k}
                placeholder={k}
                value={formData[k] || ""}
                onChange={(e) => setFormData((prev) => ({ ...prev, [k]: e.target.value }))}
                className="rounded border border-guin-muted bg-slate-950 p-2 text-sm"
              />
            ))}
          </div>
          <button
            className="mt-3 rounded bg-guin-accent px-4 py-2 font-medium text-slate-900"
            onClick={async () => setRunResult(await api.runTool(activeTool.name, formData))}
          >
            Execute
          </button>
          {runResult && (
            <pre className="mt-3 max-h-72 overflow-auto rounded bg-slate-950 p-3 text-xs">
              {JSON.stringify(runResult, null, 2)}
            </pre>
          )}
        </section>
      )}
    </div>
  );
}
