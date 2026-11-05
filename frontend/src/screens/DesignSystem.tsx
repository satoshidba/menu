import { useState } from "react";
import Icon from "../components/Icon";
import { DateBadge, Notice, Segmented, Stepper } from "../components/ui";
import { addDays, isoToday } from "../lib/format";

const SWATCHES: [string, string, string][] = [
  ["--paper", "#F7F4EE", "Warm paper — page"], ["--card", "#FFFDF9", "Raised sheets"], ["--ink", "#263029", "Deep ink — text, 12.4:1"],
  ["--quiet", "#5C665D", "Quiet grey — secondary text, 5.4:1"], ["--herb", "#3E6B53", "Herb — primary actions, 5.6:1"],
  ["--tomato", "#B65D45", "Tomato/clay — urgent marks, fills"], ["--tomato-ink", "#9E4A33", "Tomato text, 5.5:1"],
  ["--ochre", "#B9843B", "Ochre — marks only (2.98:1)"], ["--ochre-ink", "#8A5F22", "Ochre text, 5.1:1"],
];

export default function DesignSystem() {
  const [n, setN] = useState(2);
  const [seg, setSeg] = useState("all");
  const t = isoToday();
  return (
    <main className="main" style={{ paddingBottom: 64 }}>
      <div className="wordmark" style={{ marginBottom: 24 }}><Icon name="leaf" />Larder</div>
      <div className="eyebrow">Design system</div>
      <h1 style={{ marginBottom: 8 }}>A quiet kitchen journal</h1>
      <p className="muted" style={{ maxWidth: 640 }}>Food photography carries the colour. The interface stays on warm paper with deep ink, uses herb green for action, and keeps tomato and ochre for things that need attention. Every colour that signals meaning is paired with words or an icon.</p>

      <h2 className="section-title">Colour</h2>
      <div className="recipe-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 16 }}>
        {SWATCHES.map(([v, hex, label]) => (
          <div key={v}><div className="swatch" style={{ background: `var(${v})` }} /><div className="small" style={{ marginTop: 6 }}><code>{v}</code> <span className="num muted">{hex}</span></div><div className="xsmall muted">{label}</div></div>
        ))}
      </div>

      <h2 className="section-title">Type</h2>
      <div className="two-col">
        <div className="stack-sm">
          <div className="eyebrow">Newsreader — recipes and headings</div>
          <h1 style={{ fontSize: "var(--fs-3xl)" }}>Tomato & spinach farfalle</h1>
          <h2>Ingredients</h2>
          <p className="serif" style={{ fontStyle: "italic", fontSize: "1.25rem", color: "var(--ink-2)" }}>A quick sauce with wilted greens, mild enough for small children.</p>
        </div>
        <div className="stack-sm">
          <div className="eyebrow">IBM Plex Sans — controls, lists and data</div>
          <p>Body text at 16 px with 1.5 line height. Labels are short and plain: “Use soon”, “Scan item”, “Review receipt”.</p>
          <table className="ledger small"><tbody>
            <tr><td>Farfalle 500 g</td><td className="money">£0.95</td></tr><tr><td>Semi-skimmed milk 2.27 L</td><td className="money">£1.65</td></tr><tr><td>Tabular numerals align</td><td className="money">£11.10</td></tr>
          </tbody></table>
        </div>
      </div>

      <h2 className="section-title">Controls</h2>
      <div className="row-wrap">
        <button className="btn primary">Start cooking</button><button className="btn">Change this recipe</button><button className="btn ghost">Edit</button>
        <button className="btn danger">Remove</button><button className="btn primary big"><Icon name="scan" />Scan item</button>
      </div>
      <div className="row-wrap" style={{ marginTop: 16 }}>
        <Segmented label="Location" value={seg} onChange={setSeg} options={[{ value: "all", label: "All" }, { value: "fridge", label: "Fridge" }, { value: "freezer", label: "Freezer" }]} />
        <Stepper label="servings" value={n} onChange={setN} />
        <button className="chip" aria-pressed="true">Cook with what we have</button><button className="chip">30 min or less</button>
      </div>
      <div className="row-wrap" style={{ marginTop: 16 }}>
        <span className="tag herb">In stock</span><span className="tag ochre">Proposed — check</span><span className="tag tomato">Missing 2</span><span className="tag plain">v2</span>
        <DateBadge kind="use_by" date={addDays(t, 1)} /><DateBadge kind="best_before" date={addDays(t, 3)} /><DateBadge kind="best_before" date={addDays(t, 40)} />
      </div>

      <h2 className="section-title">Messages</h2>
      <div className="stack-sm" style={{ maxWidth: 640 }}>
        <Notice tone="ok">Added 4 items to the pantry and recorded 5 prices.</Notice>
        <Notice tone="warn">The source gives no amount for: Salt, to taste.</Notice>
        <Notice tone="error">This looks like a receipt you've already imported.</Notice>
        <Notice>Unknown is never counted as zero.</Notice>
      </div>

      <h2 className="section-title">Rhythm and motion</h2>
      <p className="small muted" style={{ maxWidth: 640 }}>8 px spacing scale; 1 px rules for dense data; 6/10/14 px radii; shadows only on sheets and overlays. Motion confirms a change or keeps place — a quantity nudge, a sheet rising from the bottom (sliding from the right on tablets), a receipt row settling from ochre to resolved, a step photo opening full screen and returning. Routine transitions run 120–240 ms, and <code>prefers-reduced-motion</code> removes them.</p>
      <div className="row" style={{ gap: 8 }}>{[4, 8, 16, 24, 32, 48, 64].map((s) => <div key={s} style={{ width: s, height: s, background: "var(--herb)", borderRadius: 2 }} title={`${s}px`} />)}</div>
    </main>
  );
}
