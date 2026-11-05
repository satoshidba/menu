import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import RecipeTile from "../components/RecipeTile";
import { Empty, ErrorNote, Loading, useApi } from "../components/ui";
import { minutes } from "../lib/format";
import { RecipeView } from "./RecipeDetail";

export function useWide(px = 1024) {
  const [wide, setWide] = useState(() => window.matchMedia(`(min-width: ${px}px)`).matches);
  useEffect(() => {
    const m = window.matchMedia(`(min-width: ${px}px)`);
    const h = () => setWide(m.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, [px]);
  return wide;
}

export default function Recipes() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const have = params.get("have") === "1";
  const tag = params.get("tag");
  const quick = params.get("quick") === "1";
  const qs = new URLSearchParams();
  if (q) qs.set("q", q);
  if (have) qs.set("cook_with_what_we_have", "true");
  if (tag) qs.set("tag", tag);
  if (quick) qs.set("max_minutes", "30");
  const { data, error, loading, reload } = useApi<any[]>(`/recipes?${qs}`, []);
  const wide = useWide();
  const selected = params.get("id") || (wide && data && data[0]?.id) || null;
  const tags = [...new Set((data || []).flatMap((r) => r.tags))].slice(0, 8);
  const toggle = (k: string, v: string | null) => { const n = new URLSearchParams(params); if (v === null || n.get(k) === v) n.delete(k); else n.set(k, v); n.delete("id"); setParams(n, { replace: true }); };

  const filters = (
    <div className="row-wrap" style={{ marginBottom: 16 }}>
      <button className="chip" aria-pressed={have} onClick={() => toggle("have", "1")}><Icon name="jar" size={16} />Cook with what we have</button>
      <button className="chip" aria-pressed={quick} onClick={() => toggle("quick", "1")}><Icon name="clock" size={16} />30 min or less</button>
      {tags.map((t) => <button key={t} className="chip" aria-pressed={tag === t} onClick={() => toggle("tag", t)}>{t}</button>)}
    </div>
  );

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Recipes</div><h1>{have ? "Cook with what we have" : "Our recipes"}</h1></div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); toggle("q", null); reload(); }} role="search" style={{ minWidth: "min(360px, 100%)" }}>
          <input className="input" type="search" placeholder="Search recipes" aria-label="Search recipes" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
      </div>
      {filters}
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={4} />}
      {data && data.length === 0 && (
        <Empty icon="book" title={have ? "Nothing matches your stock yet" : "No recipes yet"}>
          {have ? "Link recipe ingredients to pantry foods, or add stock, and matches will appear." : <>Paste a link, upload a photo or write one. <Link to="/recipes/new">Add a recipe</Link></>}
        </Empty>
      )}
      {data && data.length > 0 && (wide ? (
        <div className="split">
          <div className="pane-sticky" style={{ maxHeight: "calc(100dvh - 96px)", overflow: "auto" }}>
            <ul className="list">
              {data.map((r) => (
                <li key={r.id}>
                  <button className="list-row" onClick={() => { const n = new URLSearchParams(params); n.set("id", r.id); setParams(n, { replace: true }); }}
                    aria-current={selected === r.id ? "true" : undefined}
                    style={{ width: "100%", background: selected === r.id ? "var(--card)" : "transparent", border: 0, textAlign: "left", cursor: "pointer", padding: "10px 8px", boxShadow: selected === r.id ? "inset 3px 0 0 var(--herb)" : undefined }}>
                    {r.hero ? <img className="thumb" src={r.hero.url} alt="" style={{ width: 56, height: 56, objectPosition: `${r.hero.focal_x * 100}% ${r.hero.focal_y * 100}%` }} /> : <span className="thumb placeholder" style={{ width: 56, height: 56 }}><Icon name="image" size={20} /></span>}
                    <span className="grow">
                      <span className="title" style={{ display: "block", fontFamily: "var(--font-serif)", fontSize: "1.0625rem" }}>{r.title}</span>
                      <span className="meta">{minutes(r.total_minutes)} · serves {r.default_servings}{have && r.match ? ` · ${r.match.have}/${r.match.needed} in` : ""}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div>{selected && <RecipeView id={selected} embedded />}</div>
        </div>
      ) : (
        <div className="recipe-grid">{data.map((r) => <RecipeTile key={r.id} r={r} showMatch={have} />)}</div>
      ))}
    </>
  );
}
