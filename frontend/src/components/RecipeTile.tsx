import { Link } from "react-router-dom";
import { minutes } from "../lib/format";
import { Media } from "./ui";

export default function RecipeTile({ r, showMatch, width }: { r: any; showMatch?: boolean; width?: number }) {
  const m = r.match;
  return (
    <Link to={`/recipes/${r.id}`} className="recipe-tile" style={width ? { width } : undefined}>
      <Media src={r.hero?.url} alt="" ratio="ratio-card" focal={r.hero ? [r.hero.focal_x, r.hero.focal_y] : undefined} placeholder="Add a photo" />
      <h3>{r.title}</h3>
      <div className="meta">
        {minutes(r.total_minutes)} · serves {r.default_servings}
        {r.version_label && r.version_label !== "Original" ? ` · ${r.version_label}` : ""}
      </div>
      {showMatch && m && (
        <div className="small" style={{ marginTop: 4 }}>
          {m.use_soon.length > 0 && <span className="tag ochre" style={{ marginRight: 6 }}>Uses {m.use_soon.slice(0, 2).join(", ")}</span>}
          <span className="muted">{m.missing.length === 0 ? "Everything's in" : `Missing ${m.missing.slice(0, 3).join(", ")}${m.missing.length > 3 ? "…" : ""}`}</span>
        </div>
      )}
    </Link>
  );
}
