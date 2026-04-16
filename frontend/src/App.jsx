import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { useTheme } from "./hooks/useTheme";
import { DashboardPage } from "./pages/DashboardPage";
import { ProvenancePage } from "./pages/ProvenancePage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ToolsPage } from "./pages/ToolsPage";
import { ValidatePage } from "./pages/ValidatePage";

export default function App() {
  const { dark, setDark } = useTheme();
  return (
    <Layout dark={dark} onToggleTheme={() => setDark((v) => !v)}>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/run" element={<RunPage />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/validate" element={<ValidatePage />} />
        <Route path="/provenance" element={<ProvenancePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
