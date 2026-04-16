import { Link, NavLink } from "react-router-dom";

const navItems = [
  ["/", "Dashboard"],
  ["/run", "Workflow Runner"],
  ["/tools", "Tool Registry"],
  ["/validate", "BIDS Validator"],
  ["/provenance", "Provenance"],
  ["/settings", "Settings"],
];

export function Layout({ children, dark, onToggleTheme }) {
  return (
    <div className="min-h-screen bg-guin-bg text-slate-100">
      <div className="flex">
        <aside className="w-72 shrink-0 border-r border-guin-muted bg-guin-panel/80 p-4">
          <Link to="/" className="mb-6 block">
            <div className="text-xs uppercase tracking-[0.2em] text-guin-accent">GUIN</div>
            <div className="text-xl font-bold">Neuroimaging Workflow Platform</div>
          </Link>
          <nav className="space-y-1">
            {navItems.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `block rounded px-3 py-2 text-sm ${isActive ? "bg-guin-accent text-slate-900" : "hover:bg-slate-800"}`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <button
            type="button"
            onClick={onToggleTheme}
            className="mt-6 rounded border border-guin-muted px-3 py-2 text-sm"
          >
            {dark ? "Switch to Light Mode" : "Switch to Dark Mode"}
          </button>
        </aside>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
