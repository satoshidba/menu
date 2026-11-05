import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import RecipeEditor, { Content, toPayload } from "../components/RecipeEditor";
import { ErrorNote, Field, Loading, Media, Notice, Sheet, Stepper, useApi, useToast } from "../components/ui";
import { api, errorText, waitForJob } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate, isoToday, minutes, servings as fmtServings } from "../lib/format";

export default function RecipeDetail() {
  const { id } = useParams();
  return <RecipeView id={id!} />;
}

const NUTRIENT_ROWS: [string, string][] = [["energy_kcal", "Energy"], ["fat_g", "Fat"], ["saturates_g", "of which saturates"], ["carbohydrate_g", "Carbohydrate"],
  ["sugars_g", "of which sugars"], ["fibre_g", "Fibre"], ["protein_g", "Protein"], ["salt_g", "Salt"]];

export function RecipeView({ id, embedded }: { id: string; embedded?: boolean }) {
  const [params, setParams] = useSearchParams();
  const vParam = params.get("v");
  const [serves, setServes] = useState<number | null>(params.get("servings") ? parseFloat(params.get("servings")!) : null);
  const qs = new URLSearchParams();
  if (vParam) qs.set("version_id", vParam);
  if (serves) qs.set("servings", String(serves));
  const { data: r, error, loading, reload } = useApi<any>(`/recipes/${id}?${qs}`, []);
  const v = r?.version;
  const review = useApi<any>(v ? `/recipes/${id}/versions/${v.id}/review?servings=${serves || v.default_servings}` : null, [v?.id, serves]);
  const nutrition = useApi<any>(v ? `/nutrition/recipes/${v.id}?servings=${serves || v.default_servings}` : null, [v?.id, serves]);
  const [sheet, setSheet] = useState<null | "change" | "plan" | "edit" | "photo">(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const toast = useToast();

  useEffect(() => { setServes(params.get("servings") ? parseFloat(params.get("servings")!) : null); /* reset per recipe */ }, [id]); // eslint-disable-line

  if (error) return <ErrorNote error={error} retry={reload} />;
  if (loading && !r) return <Loading lines={6} />;
  if (!r || !v) return null;
  const s = serves || v.default_servings;
  const req: Record<number, any> = {};
  (review.data?.requirements || []).forEach((x: any) => { req[x.position] = x; });
  const flags = review.data?.flags;
  const setVersion = (vid: string) => { const n = new URLSearchParams(params); n.set("v", vid); setParams(n, { replace: true }); };
  const isCurrent = v.id === r.current_version_id;

  return (
    <article>
      <header className="recipe-hero" style={embedded ? { gridTemplateColumns: "1fr" } : undefined}>
        <Media src={r.hero?.url} alt={r.title} eager ratio={embedded ? "ratio-wide" : "ratio-hero"} focal={r.hero ? [r.hero.focal_x, r.hero.focal_y] : undefined}
          credit={r.hero?.attribution} creditHref={r.hero?.source_url} placeholder="No photo yet — add one from the menu below" />
        <div>
          {!embedded && <Link to="/recipes" className="small" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Icon name="left" size={16} />Recipes</Link>}
          <div className="eyebrow" style={{ marginTop: 8 }}>{[v.cuisine, ...(v.tags || [])].filter(Boolean).slice(0, 3).join(" · ")}</div>
          <h1 style={{ margin: "8px 0 12px" }}>{v.title}</h1>
          {v.description && <p className="standfirst">{v.description}</p>}
          <div className="row-wrap" style={{ margin: "12px 0" }}>
            {r.versions.filter((x: any) => x.status === "published").length > 1 ? (
              <label className="row small">Version
                <select className="input" style={{ minHeight: 36, width: "auto" }} value={v.id} onChange={(e) => setVersion(e.target.value)}>
                  {r.versions.filter((x: any) => x.status === "published").map((x: any) => (
                    <option key={x.id} value={x.id}>{x.label} (v{x.version_number}){x.id === r.current_version_id ? " — current" : ""}</option>))}
                </select>
              </label>
            ) : <span className="tag plain">{v.label}</span>}
            {!isCurrent && <button className="btn small" onClick={async () => { await api(`/recipes/${id}`, { method: "PATCH", body: { current_version_id: v.id } }); toast("Set as the current version"); reload(); }}>Make current</button>}
          </div>
          <dl className="facts">
            <div><dt>Prep</dt><dd>{minutes(v.prep_minutes)}</dd></div>
            <div><dt>Cook</dt><dd>{minutes(v.cook_minutes)}</dd></div>
            <div><dt>Serves</dt><dd><Stepper label="servings" value={s} step={s < 2 ? 0.5 : 1} min={0.5} onChange={setServes} format={fmtServings} /></dd></div>
          </dl>
          {s !== v.default_servings && <p className="xsmall muted" style={{ marginTop: 6 }}>Amounts scaled from {v.default_servings} servings for this view only; the saved recipe isn't changed.</p>}
          <div className="row-wrap" style={{ marginTop: 16 }}>
            <Link className="btn primary" to={`/recipes/${id}/cook?servings=${s}&v=${v.id}`}><Icon name="pot" />Start cooking</Link>
            <button className="btn" onClick={() => setSheet("change")}><Icon name="swap" />Change this recipe</button>
            <button className="btn" onClick={() => setSheet("plan")}><Icon name="plan" />Plan it</button>
            <button className="btn ghost" onClick={() => setSheet("edit")}><Icon name="edit" />Edit</button>
            <button className="btn ghost" onClick={() => setSheet("photo")}><Icon name="camera" />Add photo</button>
          </div>
        </div>
      </header>

      {v.unresolved_fields?.length > 0 && <div style={{ marginBottom: 24 }}><Notice tone="warn">Still unresolved from the source: {v.unresolved_fields.join(", ")}.</Notice></div>}

      <div className="recipe-body" style={embedded ? { gridTemplateColumns: "1fr" } : undefined}>
        <section className="ingredients" aria-labelledby="ing-h">
          <div className="spread"><h2 id="ing-h">Ingredients</h2><span className="xsmall muted">for {fmtServings(s)}</span></div>
          <ul className="ing-list" style={{ marginTop: 12 }}>
            {v.ingredients.map((i: any) => {
              const st = req[i.position];
              return (
                <li key={i.id} className={i.unresolved ? "unresolved" : ""}>
                  <span className="amt">{i.unresolved ? "—" : i.display}</span>
                  <span>
                    {i.name}{i.preparation ? <span className="muted">, {i.preparation}</span> : null}{i.optional && <span className="muted"> (optional)</span>}
                    <span className="row-wrap xsmall" style={{ marginTop: 2 }}>
                      {st?.status === "ok" && <span style={{ color: "var(--herb-ink)" }}>In stock</span>}
                      {st?.status === "short" && <span style={{ color: "var(--ochre-ink)" }}>Short by {st.missing_display}</span>}
                      {st?.status === "missing" && <span style={{ color: "var(--tomato-ink)" }}>Not in the pantry</span>}
                      {i.unresolved && <span style={{ color: "var(--ochre-ink)" }}>No amount in source</span>}
                      {i.proposed && <span className="tag ochre">Proposed</span>}
                      {!i.nutrition_known && !i.unresolved && <span className="muted">nutrition unknown</span>}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
          {flags && <Flags flags={flags} />}
        </section>

        <section aria-labelledby="method-h">
          <h2 id="method-h">Method</h2>
          <ol className="method" style={{ marginTop: 8 }}>
            {v.steps.map((st: any) => (
              <li key={st.id}>
                <div>
                  <p>{st.text}</p>
                  <div className="row-wrap xsmall" style={{ marginTop: 6 }}>
                    {st.timer_seconds && <span className="tag plain"><Icon name="timer" size={14} /> {Math.round(st.timer_seconds / 60)} min</span>}
                    {st.proposed && <span className="tag ochre">Proposed — check</span>}
                  </div>
                  {st.media.length > 0 && (
                    <div className="row-wrap">
                      {st.media.map((m: any) => (
                        <button key={m.id} className="step-thumb" onClick={() => setLightbox(m.url)} aria-label={`Open step ${st.position + 1} photo`}>
                          <img src={m.url} alt="" />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>

          <NutritionTable data={nutrition.data} />

          <div className="stack-sm small muted" style={{ marginTop: 24 }}>
            {v.source_url && <div>Source: <a href={v.source_url} target="_blank" rel="noreferrer">{v.source_name || new URL(v.source_url).hostname}</a>{v.source_author ? `, ${v.source_author}` : ""}{v.imported_at ? ` · imported ${fmtDate(v.imported_at)}` : ""}</div>}
            {v.change_summary && <div>This version: {v.change_summary}</div>}
            {(v.original_values?.ingredients || v.original_text) && (
              <div>
                <button className="btn ghost small" onClick={() => setShowOriginal(!showOriginal)}>{showOriginal ? "Hide" : "Show"} original wording</button>
                {showOriginal && <pre className="raw panel" style={{ marginTop: 8 }}>{v.original_text || [...(v.original_values.ingredients || []), "", ...(v.original_values.steps || [])].join("\n")}</pre>}
              </div>
            )}
          </div>
        </section>
      </div>

      {lightbox && (
        <div className="lightbox" role="dialog" aria-label="Step photo" onClick={() => setLightbox(null)}>
          <img src={lightbox} alt="" />
          <button className="btn" onClick={() => setLightbox(null)}><Icon name="close" />Close</button>
        </div>
      )}
      <ChangeSheet open={sheet === "change"} onClose={() => setSheet(null)} recipeId={id} version={v} onSaved={(vid) => { setSheet(null); setVersion(vid); reload(); }} />
      <PlanSheet open={sheet === "plan"} onClose={() => setSheet(null)} recipeId={id} versionId={v.id} servings={s} />
      <EditSheet open={sheet === "edit"} onClose={() => setSheet(null)} recipeId={id} version={v} onSaved={(vid) => { setSheet(null); setVersion(vid); reload(); }} />
      <PhotoSheet open={sheet === "photo"} onClose={() => setSheet(null)} recipeId={id} version={v} onDone={() => { setSheet(null); reload(); }} />
    </article>
  );
}

function Flags({ flags }: { flags: any }) {
  if (!flags.possible_allergens.length && !flags.person_conflicts.length && !flags.toddler.length) return null;
  return (
    <div className="panel stack-sm small" style={{ marginTop: 24 }}>
      <div className="eyebrow">Check before serving</div>
      {flags.possible_allergens.length > 0 && <div><strong>Possible allergens:</strong> {flags.possible_allergens.map((a: any) => a.allergen).join(", ")}.</div>}
      {flags.person_conflicts.map((c: any, i: number) => <div key={i} style={{ color: c.type === "allergy" ? "var(--tomato-ink)" : undefined }}>{c.message}</div>)}
      {flags.toddler.length > 0 && (
        <div>
          <strong>For the toddlers:</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{flags.toddler.map((t: any, i: number) => <li key={i}>{t.message} <span className="muted">({t.matched.slice(0, 2).join(", ")})</span></li>)}</ul>
          <a href={flags.toddler[0].source} target="_blank" rel="noreferrer">NHS guidance on foods for young children</a>
        </div>
      )}
      {flags.labels.map((l: any, i: number) => <div key={i} className="muted">Label for {l.product}: {l.allergens.length ? l.allergens.join(", ") : "no allergens recorded"}{l.traces.length ? `; may contain ${l.traces.join(", ")}` : ""}</div>)}
      <div className="xsmall muted">{flags.disclaimer}</div>
    </div>
  );
}

export function NutritionTable({ data }: { data: any }) {
  if (!data) return null;
  const per = data.per_serving.nutrients;
  const anyKnown = NUTRIENT_ROWS.some(([k]) => per[k].value !== null);
  return (
    <section style={{ marginTop: 32 }} aria-labelledby="nut-h">
      <div className="spread"><h2 id="nut-h">Nutrition per serving</h2><span className="xsmall muted">estimate</span></div>
      {!anyKnown ? <p className="muted small" style={{ marginTop: 8 }}>No nutrition data is linked to these ingredients yet.</p> : (
        <table className="ledger" style={{ marginTop: 8 }}>
          <tbody>
            {NUTRIENT_ROWS.map(([k, label]) => {
              const n = per[k];
              return (
                <tr key={k}>
                  <td style={{ paddingLeft: label.startsWith("of which") ? 20 : 8 }}>{label}</td>
                  <td className="money">{n.value === null ? <span className="muted">unknown</span> : `${k === "energy_kcal" ? Math.round(n.value) : n.value.toFixed(1)} ${n.unit}`}{k === "energy_kcal" && per.energy_kj.value !== null ? <span className="muted"> / {Math.round(per.energy_kj.value)} kJ</span> : null}</td>
                  <td className="xsmall" style={{ width: 120 }}>{n.status === "partial" ? <span style={{ color: "var(--ochre-ink)" }}>at least; {n.missing_from.length} unknown</span> : n.status === "unknown" ? "" : <span className="muted">complete</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="xsmall muted" style={{ marginTop: 8 }}>{data.basis_note} “At least” means some ingredients have no data, so the true value is higher. Unknown is never counted as zero.</p>
    </section>
  );
}

function DiffView({ diff }: { diff: any }) {
  const nut = diff.nutrition_per_serving || {};
  return (
    <div className="stack">
      <div>
        <h3>Ingredients</h3>
        <div style={{ marginTop: 8 }}>
          {diff.ingredients.filter((c: any) => c.change !== "unchanged").map((c: any, i: number) => (
            <div key={i} className={`diff-row ${c.change === "added" ? "diff-add" : c.change === "removed" ? "diff-del" : "diff-mod"}`}>
              <span className="sym" aria-hidden>{c.change === "added" ? "+" : c.change === "removed" ? "−" : "~"}</span>
              <span className="sr-only">{c.change}</span>
              <span>{c.change === "modified" ? <>{c.before_display} → <strong>{c.after_display}</strong></> : c.after_display || c.before_display}</span>
            </div>
          ))}
          <div className="xsmall muted">{diff.ingredients.filter((c: any) => c.change === "unchanged").length} unchanged</div>
        </div>
      </div>
      {diff.steps.some((s: any) => s.change !== "unchanged") && (
        <div>
          <h3>Method</h3>
          {diff.steps.filter((s: any) => s.change !== "unchanged").map((s: any, i: number) => (
            <div key={i} className={`diff-row ${s.change === "added" ? "diff-add" : s.change === "removed" ? "diff-del" : "diff-mod"}`} style={{ display: "block" }}>
              <div className="xsmall muted">Step {s.position + 1} · {s.change}</div>
              {s.before && s.change === "modified" && <div className="small muted" style={{ textDecoration: "line-through" }}>{s.before}</div>}
              <div className="small">{s.text || s.before}</div>
            </div>
          ))}
        </div>
      )}
      {(diff.allergens.added.length > 0 || diff.allergens.removed.length > 0) && (
        <Notice tone="warn">Possible allergens {diff.allergens.added.length > 0 && <>added: <strong>{diff.allergens.added.join(", ")}</strong>. </>}{diff.allergens.removed.length > 0 && <>no longer flagged: {diff.allergens.removed.join(", ")} (still check labels).</>}</Notice>
      )}
      <table className="ledger">
        <thead><tr><th>Per serving</th><th className="money">Before</th><th className="money">After</th></tr></thead>
        <tbody>{Object.entries(nut).map(([k, n]: any) => (
          <tr key={k}><td>{k.replace(/_(g|kcal)$/, "").replace("_", " ")}</td>
            <td className="money">{n.before === null ? "?" : `${Math.round(n.before * 10) / 10}`}</td>
            <td className="money">{n.after === null ? "?" : `${Math.round(n.after * 10) / 10}`} <span className="muted xsmall">{n.unit}</span></td></tr>))}
        </tbody>
      </table>
      {(diff.review_notes?.length > 0 || diff.possible_issues?.length > 0) && (
        <Notice tone="warn"><strong>Check these yourself:</strong><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{[...(diff.review_notes || []), ...(diff.possible_issues || [])].map((n: string, i: number) => <li key={i}>{n}</li>)}</ul></Notice>
      )}
    </div>
  );
}

const SUGGESTIONS = ["Make this vegetarian", "Use what we have in the pantry", "Less spicy for the toddlers", "Serve two adults and two toddlers"];

function ChangeSheet({ open, onClose, recipeId, version, onSaved }: { open: boolean; onClose: () => void; recipeId: string; version: any; onSaved: (vid: string) => void }) {
  const { me } = useAuth();
  const toast = useToast();
  const [instruction, setInstruction] = useState("");
  const [serv, setServ] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<any>(null);
  const [label, setLabel] = useState("");
  const [editing, setEditing] = useState<Content | null>(null);

  async function propose() {
    setError(null); setBusy("Working out the changes…");
    try {
      let j = await api(`/recipes/${recipeId}/propose-change`, { body: { instruction: instruction || null, servings: serv ? parseFloat(serv) : null, base_version_id: version.id } });
      if (j.status !== "succeeded" && j.status !== "failed") j = await waitForJob(j.id);
      if (j.status === "failed") throw new Error(j.error);
      setProposal(j.result); setLabel(j.result.label);
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }
  async function accept(mode: "new" | "overwrite") {
    setBusy("Saving…");
    try {
      const body: any = { draft_version_id: proposal.draft_version_id, base_version_id: version.id, label, mode };
      if (editing) Object.assign(body, { content: toPayload(editing), change_request: instruction, change_summary: proposal.summary });
      const r = await api(`/recipes/${recipeId}/versions`, { body });
      toast(mode === "overwrite" ? "Recipe updated; the previous version is kept in the history" : `Saved as “${r.version.label}”`);
      reset(); onSaved(r.version.id);
    } catch (e) { setError(errorText(e)); setBusy(null); }
  }
  async function discard() { if (proposal) await api(`/recipes/${recipeId}/drafts/${proposal.draft_version_id}`, { method: "DELETE" }).catch(() => {}); reset(); }
  function reset() { setProposal(null); setEditing(null); setInstruction(""); setServ(""); setBusy(null); setError(null); }
  async function edit() { setEditing(await api(`/recipes/${recipeId}/versions/${proposal.draft_version_id}/content`)); }

  return (
    <Sheet open={open} onClose={() => { discard(); onClose(); }} title="Change this recipe" wide>
      {!proposal ? (
        <div className="stack">
          <p className="muted small">Describe the change. You'll see exactly what would change before anything is saved, and the original stays as it is.</p>
          <div className="row-wrap">{SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => setInstruction(s)}>{s}</button>)}</div>
          <Field label="What should change?"><textarea className="input" value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="e.g. use the chicken and spinach we have" /></Field>
          {!me?.features.ai && <Notice tone="warn">Described changes need an AI provider. You can still rescale below, or edit the recipe by hand.</Notice>}
          <Field label="Or just change the servings" hint={`Currently serves ${version.default_servings}`}><input className="input num" type="number" min={0.5} step={0.5} value={serv} onChange={(e) => setServ(e.target.value)} /></Field>
          {error && <Notice tone="error">{error}</Notice>}
          <button className="btn primary big" onClick={propose} disabled={!!busy || (!instruction && !serv) || (!!instruction && !me?.features.ai && !serv)}>{busy || "Show me the changes"}</button>
        </div>
      ) : editing ? (
        <div className="stack">
          <RecipeEditor value={editing} onChange={setEditing} />
          <Field label="Version name"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="row-wrap"><button className="btn primary" onClick={() => accept("new")} disabled={!!busy}>Save as new version</button><button className="btn" onClick={() => setEditing(null)}>Back to changes</button></div>
        </div>
      ) : (
        <div className="stack">
          <Notice tone="ok">{proposal.summary}</Notice>
          <DiffView diff={proposal.diff} />
          <Field label="Name for the new version"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="row-wrap">
            <button className="btn primary" onClick={() => accept("new")} disabled={!!busy}>{busy || "Save as new version"}</button>
            <button className="btn" onClick={edit}>Edit first</button>
            <button className="btn danger" onClick={() => { if (confirm(`Replace “${version.label}” with this? The old version stays in the history.`)) accept("overwrite"); }}>Replace current</button>
            <button className="btn ghost" onClick={discard}>Discard</button>
          </div>
        </div>
      )}
    </Sheet>
  );
}

function PlanSheet({ open, onClose, recipeId, versionId, servings }: { open: boolean; onClose: () => void; recipeId: string; versionId: string; servings: number }) {
  const toast = useToast();
  const nav = useNavigate();
  const people = useApi<any[]>(open ? "/people" : null, [open]);
  const [date, setDate] = useState(isoToday());
  const [slot, setSlot] = useState("dinner");
  const [serv, setServ] = useState(servings);
  const [who, setWho] = useState<string[] | null>(null);
  const ids = who ?? (people.data || []).map((p) => p.id);
  async function save() {
    try {
      await api("/plan", { body: { date, slot, recipe_id: recipeId, recipe_version_id: versionId, servings: serv, participant_ids: ids } });
      toast({ text: "Added to the plan", action: { label: "View week", run: () => nav("/plan") } });
      onClose();
    } catch (e) { toast(errorText(e)); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Plan this meal">
      <div className="stack">
        <div className="grid-2">
          <Field label="Day"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field label="Meal"><select className="input" value={slot} onChange={(e) => setSlot(e.target.value)}>{["breakfast", "lunch", "dinner", "snack"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        </div>
        <Field label="Servings to cook"><Stepper label="servings" value={serv} step={0.5} min={0.5} onChange={setServ} format={fmtServings} /></Field>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Who's eating</div>
          <div className="row-wrap">{(people.data || []).map((p) => { const on = ids.includes(p.id); return <button key={p.id} className="chip" aria-pressed={on} onClick={() => setWho(on ? ids.filter((x) => x !== p.id) : [...ids, p.id])}>{p.name}</button>; })}</div>
        </div>
        <button className="btn primary big" onClick={save}>Add to plan</button>
      </div>
    </Sheet>
  );
}

function EditSheet({ open, onClose, recipeId, version, onSaved }: { open: boolean; onClose: () => void; recipeId: string; version: any; onSaved: (vid: string) => void }) {
  const [content, setContent] = useState<Content | null>(null);
  const [label, setLabel] = useState("Edited");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  useEffect(() => { if (open) api(`/recipes/${recipeId}/versions/${version.id}/content`).then((c) => setContent({ ...c, tags: c.tags || [] })); else setContent(null); }, [open, recipeId, version.id]);
  async function save(mode: "new" | "overwrite") {
    try {
      const r = await api(`/recipes/${recipeId}/versions`, { body: { content: toPayload(content!), base_version_id: version.id, label, mode } });
      toast("Saved. Earlier versions are kept in the history."); onSaved(r.version.id);
    } catch (e) { setError(errorText(e)); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Edit recipe" wide>
      {!content ? <Loading /> : (
        <div className="stack">
          <RecipeEditor value={content} onChange={setContent} />
          <Field label="Version name" hint="Saving creates a new version; the current one is kept."><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="row-wrap"><button className="btn primary" onClick={() => save("overwrite")}>Save changes</button><button className="btn" onClick={() => save("new")}>Save as separate version</button></div>
        </div>
      )}
    </Sheet>
  );
}

function PhotoSheet({ open, onClose, recipeId, version, onDone }: { open: boolean; onClose: () => void; recipeId: string; version: any; onDone: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  async function upload() {
    const f = file.current?.files?.[0];
    if (!f) { setError("Choose a photo"); return; }
    const fd = new FormData();
    fd.append("file", f);
    if (step !== "") { fd.append("step_position", step); fd.append("version_id", version.id); } else fd.append("make_hero", "true");
    try { await api(`/recipes/${recipeId}/media`, { form: fd }); toast("Photo added"); onDone(); } catch (e) { setError(errorText(e)); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Add a photo">
      <div className="stack">
        <Field label="Photo"><input ref={file} className="input" type="file" accept="image/*" capture="environment" /></Field>
        <Field label="Where does it go?">
          <select className="input" value={step} onChange={(e) => setStep(e.target.value)}>
            <option value="">Main dish photo</option>
            {version.steps.map((s: any) => <option key={s.id} value={s.position}>Step {s.position + 1}: {s.text.slice(0, 40)}…</option>)}
          </select>
        </Field>
        <p className="xsmall muted">Photos are re-saved without location data and are only visible to your household.</p>
        {error && <Notice tone="error">{error}</Notice>}
        <button className="btn primary big" onClick={upload}>Upload</button>
      </div>
    </Sheet>
  );
}
