import { FormEvent, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../components/Icon";
import RecipeEditor, { Content, toPayload } from "../components/RecipeEditor";
import { Field, Notice, Segmented, useApi, useToast } from "../components/ui";
import { api, errorText, waitForJob } from "../lib/api";
import { useAuth } from "../lib/auth";

type Mode = "link" | "text" | "photo" | "idea" | "manual";
const BLANK: Content = { title: "", tags: [], default_servings: 4, ingredients: [{ name: "", quantity: null, unit: "g" }], steps: [{ text: "" }] };

export default function RecipeNew() {
  const { me } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("link");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [prompt, setPrompt] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ content: Content; images: any[]; warnings: string[]; method?: string; ai_used?: boolean; missing?: string[] } | null>(null);
  const [hero, setHero] = useState<string | null>(null);
  const files = useRef<HTMLInputElement>(null);
  const pantry = useApi<any>(mode === "idea" ? "/pantry" : null, [mode]);

  async function run(start: () => Promise<any>) {
    setError(null);
    setBusy("Reading the recipe…");
    try {
      const job = await start();
      const done = job.status === "succeeded" || job.status === "failed" ? job : await waitForJob(job.id, () => setBusy("Still working…"));
      if (done.status === "failed") throw new Error(done.error || "Import failed");
      const r = done.result;
      setDraft({ content: { ...BLANK, ...r.draft, tags: r.draft.tags || [] }, images: r.images || [], warnings: r.warnings || [], method: r.method, ai_used: r.ai_used, missing: r.missing_amounts });
      setHero((r.images || []).find((i: any) => i.role === "hero")?.staging_path || null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (mode === "link") run(() => api("/recipes/import", { body: { url } }));
    if (mode === "text") run(() => api("/recipes/import", { body: { text } }));
    if (mode === "idea") run(() => api("/recipes/vibe", { body: { prompt, food_ids: picked } }));
    if (mode === "photo") {
      const fd = new FormData();
      Array.from(files.current?.files || []).forEach((f) => fd.append("files", f));
      if (!fd.has("files")) { setError("Choose one or more photos first."); return; }
      run(() => api("/recipes/import/photos", { form: fd }));
    }
    if (mode === "manual") setDraft({ content: { ...BLANK }, images: [], warnings: [] });
  }

  async function save() {
    if (!draft) return;
    setBusy("Saving…");
    try {
      const media = draft.images.map((im) => ({ staging_path: im.staging_path, source_url: im.source_url, role: im.staging_path === hero ? "hero" : im.role === "hero" ? "photo" : im.role, step_index: im.step_index }));
      media.sort((a) => (a.role === "hero" ? -1 : 1));
      const r = await api("/recipes", { body: { content: toPayload(draft.content), media } });
      toast("Recipe saved");
      nav(`/recipes/${r.id}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  if (draft) {
    const c = draft.content;
    const unresolved = c.unresolved_fields || [];
    return (
      <>
        <div className="page-head">
          <div><div className="eyebrow">Review before saving</div><h1>{c.title || "New recipe"}</h1></div>
          <div className="row"><button className="btn" onClick={() => setDraft(null)}>Start again</button><button className="btn primary" onClick={save} disabled={!!busy || !c.title}>{busy || "Save recipe"}</button></div>
        </div>
        <div className="stack" style={{ maxWidth: 820 }}>
          {draft.method && (
            <Notice tone={unresolved.length ? "warn" : "ok"}>
              {draft.method.startsWith("json-ld") ? "Read from the page's structured recipe data." : draft.method === "ai-idea" ? "This is a suggestion, not a sourced recipe." : "Read from the text."}
              {draft.ai_used && " AI helped with parts the page didn't structure; those are marked “Proposed”."}
              {unresolved.length > 0 && <> Still to check: <strong>{unresolved.join(", ")}</strong>.</>}
              {draft.missing && draft.missing.length > 0 && <> The source gives no amount for: {draft.missing.join("; ")}.</>}
            </Notice>
          )}
          {draft.warnings.map((w, i) => <Notice key={i} tone="warn">{w}</Notice>)}
          {error && <Notice tone="error">{error}</Notice>}
          <RecipeEditor value={c} onChange={(v) => setDraft({ ...draft, content: v })} images={draft.images} hero={hero} onHero={setHero} />
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary big" onClick={save} disabled={!!busy || !c.title}>{busy || "Save recipe"}</button></div>
        </div>
      </>
    );
  }

  const items = (pantry.data?.items || []) as any[];
  return (
    <>
      <div className="page-head"><div><div className="eyebrow">Recipes</div><h1>Add a recipe</h1></div></div>
      <form onSubmit={submit} className="stack" style={{ maxWidth: 720 }}>
        <Segmented<Mode> label="How to add" value={mode} onChange={setMode} options={[
          { value: "link", label: "Link" }, { value: "text", label: "Paste text" }, { value: "photo", label: "Photos" },
          { value: "idea", label: "Cook with what we have" }, { value: "manual", label: "Write it" }]} />
        {mode === "link" && <Field label="Recipe link" hint="We read the page's recipe data first and only ask AI for help if the page isn't structured."><input className="input" type="url" required placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>}
        {mode === "text" && <Field label="Recipe text" hint="Paste ingredients and method; headings like “Ingredients” and “Method” help."><textarea className="input" style={{ minHeight: 220 }} required value={text} onChange={(e) => setText(e.target.value)} /></Field>}
        {mode === "photo" && <Field label="Photos of the recipe" hint="Cookbook pages or a handwritten card; up to 8."><input ref={files} className="input" type="file" accept="image/*" multiple capture="environment" /></Field>}
        {mode === "idea" && (
          <>
            {!me?.features.ai && <Notice tone="warn">Recipe ideas need an AI provider, which isn't set up on this server. You can still write a recipe by hand.</Notice>}
            <Field label="What would you like?" hint="For example: “quick dinner, mild for the toddlers, uses the spinach”"><textarea className="input" style={{ minHeight: 90 }} required value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
            <div>
              <div className="eyebrow" style={{ marginBottom: 8 }}>Use these (optional)</div>
              <div className="row-wrap">
                {items.filter((g) => g.food).slice(0, 30).map((g) => {
                  const on = picked.includes(g.food.id);
                  return <button type="button" key={g.key} className="chip" aria-pressed={on} onClick={() => setPicked(on ? picked.filter((x) => x !== g.food.id) : [...picked, g.food.id])}>{g.name}</button>;
                })}
              </div>
            </div>
            <p className="xsmall muted">Ideas are suggestions: every amount and step is marked for you to check, and allergens are flagged for you to confirm on the labels.</p>
          </>
        )}
        {mode === "manual" && <p className="muted">Start with a blank recipe.</p>}
        {error && <Notice tone="error">{error}</Notice>}
        <div><button className="btn primary big" disabled={!!busy || (mode === "idea" && !me?.features.ai)}><Icon name={mode === "manual" ? "edit" : "check"} />{busy || (mode === "manual" ? "Start writing" : "Continue to review")}</button></div>
      </form>
    </>
  );
}
