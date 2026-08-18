// Full-page wrapper for the vessel entity view (route /vessels/:id).
//
// Same document as the map drawer, one width wider — the panel component is
// shared so the two can never drift into two different vessel screens.
import { useParams, useNavigate, Link } from "react-router-dom";
import { VesselPanel } from "../components/VesselPanel.jsx";

export function VesselPage() {
  const { id } = useParams();
  const nav = useNavigate();
  return (
    <div className="scroll-y">
      <div className="toolbar">
        <Link className="btn btn-sm" to="/vessels">← Vessels</Link>
        <div className="eyebrow">Vessel entity</div>
      </div>
      <div className="pad">
        <div className="card card-pad" style={{ maxWidth: 760, padding: "22px 26px" }}>
          <VesselPanel
            vesselId={id}
            onOpenGraph={(vid) => nav(`/graph?seed=${encodeURIComponent(vid)}`)}
          />
        </div>
      </div>
    </div>
  );
}
