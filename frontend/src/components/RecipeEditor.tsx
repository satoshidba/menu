import { useState } from "react";
import { api, errorText } from "../lib/api";
import Icon from "./Icon";
import { Field, Notice, useToast } from "./ui";

export type Ing = {
  original_text?: string | null; name: string; quantity: number | null; quantity_max?: number | null; unit: string | null; preparation?: string | null;
  optional?: boolean; food_id?: string | null; food_name?: string | null; product_id?: string | null; proposed?: boolean; provenance?: string; confidence?: number | null;
  unresolved?: boolean; needs_confirmation?: boolean; note?: string | null; food_suggestions?: { id: string; name: string }[]; section?: string | null;
};
export type Step = { text: string; original_text?: string | null; timer_seconds?: number | null; proposed?: boolean; provenance?: string };
export type Content = {
  title: string; description?: string | null; cuisine?: string | null; tags: string[]; prep_minutes?: number | null; cook_minutes?: number | null;
  total_minutes?: number | null; yield_text?: string | null; default_servings: number; notes?: string | null; ingredients: Ing[]; steps: Step[];
  source_url?: string | null; source_name?: string | null; source_author?: string | null; original_text?: string | null; original_values?: any;
  provenance?: string; extraction_confidence?: number | null; unresolved_fields?: string[];
};

const UNITS = ["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "each", "clove", "tin", "pack", "pinch", "bunch", "slice", "handful"];

export function toPayload(c: Content): any {
  return {
    ...c,
    ingredients: c.ingredients.filter((i) => i.name.trim()).map((i) => ({
      original_text: i.original_text, name: i.name.trim(), quantity: i.quantity, quantity_max: i.quantity_max ?? null, unit: i.unit || null,
      preparation: i.preparation || null, optional: !!i.optional, food_id: i.food_id || null, product_id: i.product_id || null,
      proposed: !!i.proposed, provenance: i.provenance || "manual", confidence: i.confidence ?? null, section: i.section || null, note: i.note || null,
    })),
    steps: c.steps.filter((s) => s.text.trim()).map((s) => ({ text: s.text.trim(), original_text: s.original_text ?? null, timer_seconds: s.timer_seconds ?? null,
      proposed: !!s.proposed, provenance: s.provenance || "manual" })),
    tags: c.tags.filter(Boolean),
  };
}

export default function RecipeEditor({ value, onChange, images, hero, onHero }: {
  value: Content; onChange: (c: Content) => void; images?: any[]; hero?: string | null; onHero?: (p: string) => void;
}) {
  const toast = useToast();
  const set = (patch: Partial<Content>) => onChange({ ...value, ...patch });
  const setIng = (i: number, patch: Partial<Ing>) => set({ ingredients: value.ingredients.map((x, k) => (k === i ? { ...x, ...patch } : x)) });
  const setStep = (i: number, patch: Partial<Step>) => set({ steps: value.steps.map((x, k) => (k === i ? { ...x, ...patch } : x)) });
  const [cupGrams, setCupGrams] = useState<Record<number, string>>({});

  async function confirmCup(i: number) {
    const ing = value.ingredients[i];
    const g = parseFloat(cupGrams[i]);
    if (!ing.food_id || !g) { toast("Link the ingredient to a food and enter the grams first."); return; }
    try {
      await api(`/foods/${ing.food_id}/confirm-conversion`, { body: { unit: ing.unit, grams: g } });
      setIng(i, { quantity: Math.round((ing.quantity || 0) * g), unit: "g", needs_confirmation: false, note: `${ing.quantity} ${ing.unit} = ${Math.round((ing.quantity || 0) * g)} g (confirmed)` });
      toast(`Saved: 1 ${ing.unit} of ${ing.food_name || ing.name} = ${g} g`);
    } catch (e) { toast(errorText(e)); }
  }

  return (
    <div className="stack">
      <Field label="Title"><input className="input" value={value.title} onChange={(e) => set({ title: e.target.value })} required /></Field>
      <Field label="Short description"><textarea className="input" style={{ minHeight: 72 }} value={value.description || ""} onChange={(e) => set({ description: e.target.value })} /></Field>
      <div className="grid-3">
        <Field label="Serves"><input className="input num" type="number" min={1} step={0.5} value={value.default_servings} onChange={(e) => set({ default_servings: parseFloat(e.target.value) || 1 })} /></Field>
        <Field label="Prep (min)"><input className="input num" type="number" min={0} value={value.prep_minutes ?? ""} onChange={(e) => set({ prep_minutes: e.target.value ? parseInt(e.target.value) : null })} /></Field>
        <Field label="Cook (min)"><input className="input num" type="number" min={0} value={value.cook_minutes ?? ""} onChange={(e) => set({ cook_minutes: e.target.value ? parseInt(e.target.value) : null })} /></Field>
      </div>
      <Field label="Tags" hint="Separate with commas, e.g. vegetarian, weeknight"><input className="input" value={value.tags.join(", ")} onChange={(e) => set({ tags: e.target.value.split(",").map((t) => t.trim()) })} /></Field>

      {images && images.length > 0 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Photos from the source</div>
          <div className="row-wrap">
            {images.map((im) => (
              <button key={im.staging_path} type="button" onClick={() => onHero?.(im.staging_path)} className="step-thumb"
                style={{ outline: hero === im.staging_path ? "3px solid var(--herb)" : undefined, width: 132 }} aria-pressed={hero === im.staging_path}
                aria-label={`${im.role === "step" ? `Step ${im.step_index + 1} photo` : "Dish photo"}${hero === im.staging_path ? " (main photo)" : ""}`}>
                <img src={im.url} alt="" />
              </button>
            ))}
          </div>
          <p className="xsmall muted" style={{ marginTop: 6 }}>Tap a photo to use it as the main image. Step photos are attached to their steps.</p>
        </div>
      )}

      <div>
        <div className="spread"><h3>Ingredients</h3><span className="xsmall muted">Amounts in UK metric; source wording is kept</span></div>
        <ul className="list" style={{ marginTop: 8 }}>
          {value.ingredients.map((ing, i) => (
            <li key={i} style={{ padding: "10px 0", background: ing.unresolved && !ing.optional && ing.quantity === null ? "color-mix(in srgb, var(--ochre-tint) 55%, transparent)" : undefined }}>
              <div style={{ display: "grid", gridTemplateColumns: "88px 96px 1fr auto", gap: 8, alignItems: "center" }}>
                <input className={`input num ${ing.quantity === null && !ing.optional ? "invalid" : ""}`} type="number" min={0} step="any" aria-label="Amount" placeholder="?"
                  value={ing.quantity ?? ""} onChange={(e) => setIng(i, { quantity: e.target.value === "" ? null : parseFloat(e.target.value), unresolved: e.target.value === "" })} />
                <select className="input" aria-label="Unit" value={ing.unit || ""} onChange={(e) => setIng(i, { unit: e.target.value || null })}>
                  <option value="">—</option>
                  {[...new Set([...(ing.unit ? [ing.unit] : []), ...UNITS])].map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
                <input className="input" aria-label="Ingredient" value={ing.name} onChange={(e) => setIng(i, { name: e.target.value, food_id: null, food_name: null })} />
                <button type="button" className="btn ghost icon-btn" aria-label={`Remove ${ing.name}`} onClick={() => set({ ingredients: value.ingredients.filter((_, k) => k !== i) })}><Icon name="trash" size={18} /></button>
              </div>
              <div className="row-wrap xsmall" style={{ marginTop: 6, alignItems: "center" }}>
                {ing.original_text && ing.original_text !== ing.name && <span className="muted">Source: “{ing.original_text}”</span>}
                {ing.proposed && <span className="tag ochre">Proposed — check</span>}
                {ing.quantity === null && !ing.optional && <span className="tag ochre">Amount missing from source</span>}
                {ing.food_name ? <span className="tag herb">Linked: {ing.food_name}</span> : ing.food_suggestions && ing.food_suggestions.length > 0 && (
                  <label className="row">Link to
                    <select className="input" style={{ minHeight: 32, padding: "2px 8px", width: "auto" }} value="" onChange={(e) => { const s = ing.food_suggestions!.find((x) => x.id === e.target.value); if (s) setIng(i, { food_id: s.id, food_name: s.name }); }}>
                      <option value="">new food “{ing.name}”</option>
                      {ing.food_suggestions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  </label>
                )}
                <label className="row"><input type="checkbox" checked={!!ing.optional} onChange={(e) => setIng(i, { optional: e.target.checked })} /> optional</label>
                {ing.note && <span className="muted">{ing.note}</span>}
              </div>
              {ing.needs_confirmation && (
                <div className="row-wrap small" style={{ marginTop: 8 }}>
                  <span>1 {ing.unit} of {ing.food_name || ing.name} weighs</span>
                  <input className="input num" style={{ width: 90, minHeight: 36 }} type="number" min={1} value={cupGrams[i] || ""} onChange={(e) => setCupGrams({ ...cupGrams, [i]: e.target.value })} aria-label="grams per unit" />
                  <span>g</span>
                  <button type="button" className="btn small" onClick={() => confirmCup(i)}>Confirm weight</button>
                  <span className="muted xsmall">Weight depends on the ingredient, so we ask rather than guess.</span>
                </div>
              )}
            </li>
          ))}
        </ul>
        <button type="button" className="btn small" style={{ marginTop: 8 }} onClick={() => set({ ingredients: [...value.ingredients, { name: "", quantity: null, unit: "g" }] })}><Icon name="plus" size={18} />Add ingredient</button>
      </div>

      <div>
        <h3>Method</h3>
        <ol className="list" style={{ marginTop: 8, paddingLeft: 0, listStyle: "none" }}>
          {value.steps.map((s, i) => (
            <li key={i} style={{ padding: "10px 0", display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 8 }}>
              <span className="serif" style={{ fontSize: "1.25rem", color: "var(--herb)" }}>{i + 1}</span>
              <div className="stack-sm">
                <textarea className="input" style={{ minHeight: 76 }} aria-label={`Step ${i + 1}`} value={s.text} onChange={(e) => setStep(i, { text: e.target.value })} />
                <div className="row xsmall">
                  <label className="row">Timer <input className="input num" style={{ width: 72, minHeight: 32 }} type="number" min={0} value={s.timer_seconds ? Math.round(s.timer_seconds / 60) : ""}
                    onChange={(e) => setStep(i, { timer_seconds: e.target.value ? parseInt(e.target.value) * 60 : null })} aria-label="Timer minutes" /> min</label>
                  {s.proposed && <span className="tag ochre">Proposed — check</span>}
                </div>
              </div>
              <button type="button" className="btn ghost icon-btn" aria-label={`Remove step ${i + 1}`} onClick={() => set({ steps: value.steps.filter((_, k) => k !== i) })}><Icon name="trash" size={18} /></button>
            </li>
          ))}
        </ol>
        <button type="button" className="btn small" style={{ marginTop: 8 }} onClick={() => set({ steps: [...value.steps, { text: "" }] })}><Icon name="plus" size={18} />Add step</button>
      </div>
      <Field label="Notes"><textarea className="input" style={{ minHeight: 72 }} value={value.notes || ""} onChange={(e) => set({ notes: e.target.value })} /></Field>
      {value.source_url && <Notice>Source: <a href={value.source_url} target="_blank" rel="noreferrer">{value.source_name || value.source_url}</a>{value.source_author ? ` · by ${value.source_author}` : ""}. The source link and original wording are kept with the recipe.</Notice>}
    </div>
  );
}
