import { useEffect, useState } from "react";

import { api } from "../api/client";

export function SettingsPage() {
  const [cfg, setCfg] = useState({
    container_dir: "",
    model: "",
    api_key: "",
    bids_validator_path: "",
    apptainer_binary: "",
  });
  const [containers, setContainers] = useState([]);

  useEffect(() => {
    api
      .getConfig()
      .then((d) => {
        setCfg((prev) => ({ ...prev, ...(d.config || {}) }));
        setContainers(d.containers || []);
      })
      .catch(console.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSave = async () => {
    const d = await api.putConfig(cfg);
    setCfg(d.config);
    setContainers(d.containers || []);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Settings</h1>
      {Object.keys(cfg).map((k) => (
        <label key={k} className="block text-sm">
          <span className="mb-1 block text-slate-300">{k}</span>
          <input
            value={cfg[k] || ""}
            onChange={(e) => setCfg((prev) => ({ ...prev, [k]: e.target.value }))}
            className="w-full rounded border border-guin-muted bg-slate-950 p-2"
          />
        </label>
      ))}
      <button className="rounded bg-guin-accent px-4 py-2 text-slate-900" onClick={onSave}>
        Save
      </button>

      <section className="rounded border border-guin-muted bg-slate-900/50 p-4">
        <h2 className="mb-3 text-lg font-medium">Container Manager</h2>
        <div className="space-y-2 text-sm">
          {containers.map((c) => (
            <div key={c.path} className="rounded bg-slate-800/60 px-3 py-2">
              {c.name} ({Math.round((c.size_bytes || 0) / (1024 * 1024))} MB)
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
