// Full-page wrapper for the vessel entity view (route /vessels/:id).
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
      <div className="pad" style={{ maxWidth: 720 }}>
        <VesselPanel vesselId={id} onOpenGraph={(vid) => nav(`/graph?seed=${encodeURIComponent(vid)}`)} />
      </div>
    </div>
  );
}
