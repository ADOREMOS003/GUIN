import { useState } from "react";

import { api } from "../api/client";

export function ValidatePage() {
  const [datasetPath, setDatasetPath] = useState("");
  const [result, setResult] = useState(null);

  const onValidate = async () => {
    const res = await api.validate(datasetPath);
    setResult(res);
  };

  const valid = result?.validation?.valid;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">BIDS Validator</h1>
      <div className="flex gap-3">
        <input
          value={datasetPath}
          onChange={(e) => setDatasetPath(e.target.value)}
          placeholder="Dataset path"
          className="flex-1 rounded border border-guin-muted bg-slate-950 p-2 text-sm"
        />
        <button className="rounded bg-guin-accent px-4 py-2 font-medium text-slate-900" onClick={onValidate}>
          Validate
        </button>
      </div>

      {result && (
        <div className={`rounded border p-4 ${valid ? "border-emerald-500/40 bg-emerald-900/20" : "border-red-500/40 bg-red-900/20"}`}>
          <div className="font-medium">{valid ? "Validation passed" : "Validation failed"}</div>
          <div className="mt-2 text-sm">
            Files: {result.summary?.files ?? 0} | Subjects: {result.summary?.subjects ?? 0} | Sessions:{" "}
            {result.summary?.sessions ?? 0}
          </div>
          <details className="mt-2">
            <summary className="cursor-pointer text-sm">Errors</summary>
            <pre className="mt-1 rounded bg-slate-950 p-2 text-xs">{JSON.stringify(result.validation?.errors || [], null, 2)}</pre>
          </details>
          <details className="mt-2">
            <summary className="cursor-pointer text-sm">Warnings</summary>
            <pre className="mt-1 rounded bg-slate-950 p-2 text-xs">{JSON.stringify(result.validation?.warnings || [], null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}
