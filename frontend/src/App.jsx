import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api.js";
import { MapView } from "./views/MapView.jsx";
import { FindingsView } from "./views/FindingsView.jsx";
import { AlertsView } from "./views/AlertsView.jsx";
import { VesselsView } from "./views/VesselsView.jsx";
import { VesselPage } from "./views/VesselPage.jsx";
import { GraphView } from "./views/GraphView.jsx";

export function App() {
  const [health, setHealth] = useState("checking");

  useEffect(() => {
    api.health().then(() => setHealth("ok")).catch(() => setHealth("down"));
  }, []);

  return (
    <div className="app">
      <nav className="topnav">
        <div className="brand">
          <span className="mark">Maritime ISR</span>
          <span className="sub">Arabian Sea</span>
        </div>
        <NavLink to="/" end className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Map</NavLink>
        {/* Findings sits directly after the map: it is the answer to "what did
            you find?", and the ranked table is what the landed data supports. */}
        <NavLink to="/findings" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Findings</NavLink>
        <NavLink to="/alerts" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Alerts</NavLink>
        <NavLink to="/vessels" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Vessels</NavLink>
        <NavLink to="/graph" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Graph</NavLink>
        <div className="nav-spacer" />
        {health === "down" && (
          <span className="badge badge-finding" style={{ marginLeft: 10 }}>
            API unreachable
          </span>
        )}
      </nav>
      <div className="main">
        <Routes>
          <Route path="/" element={<MapView />} />
          <Route path="/findings" element={<FindingsView />} />
          <Route path="/alerts" element={<AlertsView />} />
          <Route path="/vessels" element={<VesselsView />} />
          <Route path="/vessels/:id" element={<VesselPage />} />
          <Route path="/graph" element={<GraphView />} />
        </Routes>
      </div>
    </div>
  );
}
