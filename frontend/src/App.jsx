import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api.js";
import { useTheme } from "./lib/theme.js";
import { MapView } from "./views/MapView.jsx";
import { VesselsView } from "./views/VesselsView.jsx";
import { VesselPage } from "./views/VesselPage.jsx";
import { GraphView } from "./views/GraphView.jsx";
import { RadarView } from "./views/RadarView.jsx";
import { WatchView } from "./views/WatchView.jsx";

//: Cycles system, light, dark. The label names what is on screen now, and the
//: title says what the next press does, so the control is legible without
//: pressing it.
function ThemeToggle() {
  const { pref, resolved, setTheme } = useTheme();
  const next = { system: "light", light: "dark", dark: "system" }[pref];
  const icon = { light: "\u2600", dark: "\u263E", system: "\u25D0" }[resolved === "dark" && pref === "system" ? "system" : pref];
  return (
    <button className="theme-toggle" onClick={() => setTheme(next)}
            title={`Theme: ${pref}. Press for ${next}.`}>
      <span aria-hidden="true">{icon}</span>
      {pref === "system" ? "System" : pref === "dark" ? "Dark" : "Light"}
    </button>
  );
}

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
        {/* One tab where three used to be. Assistant, Findings and Alerts were
            substantially the same facts three times over: the assistant
            already ranked every subject and carried the evidence, findings
            re-listed a subset of the same hulls, and alerts held the same
            detections again while being the only place any of it could be
            acted on. Watch keeps both readings (by vessel, by event) and puts
            the disposition buttons in both, so recording a decision never
            needs a change of screen. */}
        <NavLink to="/watch" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Watch</NavLink>
        <NavLink to="/radar" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Radar</NavLink>
        <NavLink to="/vessels" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Vessels</NavLink>
        <NavLink to="/graph" className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}>Graph</NavLink>
        <div className="nav-spacer" />
        {health === "down" && (
          <span className="badge badge-finding" style={{ marginLeft: 10 }}>
            API unreachable
          </span>
        )}
        <ThemeToggle />
      </nav>
      <div className="main">
        <Routes>
          <Route path="/" element={<MapView />} />
          <Route path="/watch" element={<WatchView />} />
          {/* The three old paths still resolve. Somebody has a tab open, a
              bookmark, or a link in a handover note, and a dead URL in an
              operations tool reads as the tool being broken. */}
          <Route path="/assistant" element={<Navigate to="/watch" replace />} />
          <Route path="/findings" element={<Navigate to="/watch" replace />} />
          <Route path="/alerts" element={<Navigate to="/watch?by=event" replace />} />
          <Route path="/radar" element={<RadarView />} />
          <Route path="/vessels" element={<VesselsView />} />
          <Route path="/vessels/:id" element={<VesselPage />} />
          <Route path="/graph" element={<GraphView />} />
        </Routes>
      </div>
    </div>
  );
}
