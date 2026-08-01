import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api.js";
import { MapView } from "./views/MapView.jsx";
import { AlertsView } from "./views/AlertsView.jsx";
import { VesselsView } from "./views/VesselsView.jsx";
import { VesselPage } from "./views/VesselPage.jsx";
import { GraphView } from "./views/GraphView.jsx";

export function App() {
  const [scn, setScn] = useState(null);
  const [health, setHealth] = useState("checking");

  useEffect(() => {
    api.health().then(() => setHealth("ok")).catch(() => setHealth("down"));
    api.stats().then((s) => {
      const syn =
        s.vessels.synthetic + s.alerts.synthetic +
        Object.values(s.events).reduce((a, e) => a + e.synthetic, 0);
      setScn({ vessels: s.vessels, alerts: s.alerts, syn });
    }).catch(() => {});
  }, []);

  return (
    <div className="app">
      <nav className="topnav">
        <div className="brand">
          <span className="mark">Maritime ISR</span>
          <span className="sub">Arabian Sea</span>
        </div>
        <NavLink to="/" end className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Map</NavLink>
        <NavLink to="/alerts" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Alerts</NavLink>
        <NavLink to="/vessels" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Vessels</NavLink>
        <NavLink to="/graph" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Graph</NavLink>
        <div className="nav-spacer" />
        {scn && (
          <span
            className="scenario-flag"
            title="This picture contains synthetic scenario data (ADR-019). Real and scenario figures are always shown separately."
          >
            <span className="dot" />
            Scenario data included · {scn.vessels.synthetic} vessels, {scn.alerts.synthetic} alerts
          </span>
        )}
        {health === "down" && (
          <span className="badge badge-finding" style={{ marginLeft: 10 }}>
            API unreachable
          </span>
        )}
      </nav>
      <div className="main">
        <Routes>
          <Route path="/" element={<MapView />} />
          <Route path="/alerts" element={<AlertsView />} />
          <Route path="/vessels" element={<VesselsView />} />
          <Route path="/vessels/:id" element={<VesselPage />} />
          <Route path="/graph" element={<GraphView />} />
        </Routes>
      </div>
    </div>
  );
}
