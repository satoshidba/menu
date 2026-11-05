import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import { Field, Loading, Media, Notice, Sheet, Stepper, useApi, useToast } from "../components/ui";
import { api, errorText, uid } from "../lib/api";
import { servings as fmtServings } from "../lib/format";

function useTimer() {
  const [left, setLeft] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    if (!running || left === null) return;
    if (left <= 0) {
      setRunning(false);
      navigator.vibrate?.([300, 150, 300]);
      try { const ctx = new AudioContext(); const o = ctx.createOscillator(); o.frequency.value = 880; o.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.6); } catch { /* no audio */ }
      return;
    }
    const t = setTimeout(() => setLeft((l) => (l ?? 1) - 1), 1000);
    return () => clearTimeout(t);
  }, [running, left]);
  return { left, running, start: (s: number) => { setLeft(s); setRunning(true); }, toggle: () => setRunning((r) => !r), reset: () => { setLeft(null); setRunning(false); } };
}
const mmss = (s: number) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

export default function CookMode() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const servings = parseFloat(params.get("servings") || "0") || undefined;
  const vid = params.get("v");
  const { data: r } = useApi<any>(`/recipes/${id}?${vid ? `version_id=${vid}&` : ""}${servings ? `servings=${servings}` : ""}`);
  const [i, setI] = useState(0);
  const [showIngs, setShowIngs] = useState(false);
  const [finish, setFinish] = useState(false);
  const [zoom, setZoom] = useState(false);
  const timer = useTimer();
  const touch = useRef<number | null>(null);

  useEffect(() => {
    let lock: any;
    (navigator as any).wakeLock?.request?.("screen").then((l: any) => (lock = l)).catch(() => {});
    return () => { lock?.release?.(); };
  }, []);
  const v = r?.version;
  const steps = v?.steps || [];
  const last = steps.length - 1;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (finish || zoom) return;
      if (e.key === "ArrowRight") setI((x) => Math.min(last, x + 1));
      if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last, finish, zoom]);
  useEffect(() => { timer.reset(); }, [i]); // eslint-disable-line

  if (!v) return <div className="cook"><div /><div className="cook-stage"><Loading lines={4} /></div><div /></div>;
  const step = steps[i];
  const stepPhoto = step?.media?.[0] || null;
  const photo = stepPhoto || r.hero;
  const s = servings || v.default_servings;

  return (
    <div className="cook" onTouchStart={(e) => (touch.current = e.touches[0].clientX)}
      onTouchEnd={(e) => { if (touch.current === null) return; const dx = e.changedTouches[0].clientX - touch.current; if (Math.abs(dx) > 60) setI((x) => Math.min(last, Math.max(0, x + (dx < 0 ? 1 : -1)))); touch.current = null; }}>
      <div className="cook-top">
        <button className="btn ghost" onClick={() => nav(-1)} aria-label="Leave cooking mode"><Icon name="close" />Leave</button>
        <div className="cook-progress" role="progressbar" aria-valuemin={1} aria-valuemax={steps.length} aria-valuenow={i + 1} aria-label="Progress"><span style={{ width: `${((i + 1) / steps.length) * 100}%` }} /></div>
        <button className="btn ghost" onClick={() => setShowIngs(!showIngs)} aria-expanded={showIngs}><Icon name="jar" />{showIngs ? "Hide" : "Ingredients"}</button>
      </div>

      <div className="cook-stage">
        <div>
          <div className="cook-step-num">Step {i + 1} of {steps.length} · {v.title}</div>
          <p key={i} className="cook-step-text enter" aria-live="polite">{step.text}</p>
          {step.proposed && <p className="small" style={{ marginTop: 12 }}><span className="tag ochre">Proposed step — check before relying on it</span></p>}
          {step.timer_seconds && (
            <div className="row-wrap" style={{ marginTop: 24 }}>
              {timer.left === null ? (
                <button className="btn big" onClick={() => timer.start(step.timer_seconds)}><Icon name="timer" />Start {Math.round(step.timer_seconds / 60)} min timer</button>
              ) : (
                <div className={`timer ${timer.left <= 0 ? "done" : ""}`} role="timer" aria-live="assertive">
                  <Icon name="timer" />{timer.left <= 0 ? "Time's up" : mmss(timer.left)}
                  <button className="btn small" onClick={timer.toggle}>{timer.running ? "Pause" : "Resume"}</button>
                  <button className="btn small ghost" onClick={timer.reset}>Reset</button>
                </div>
              )}
            </div>
          )}
          {showIngs && (
            <ul className="ing-list cook-ings" style={{ marginTop: 24 }}>
              {v.ingredients.map((x: any) => <li key={x.id}><span className="amt">{x.unresolved ? "—" : x.display}</span><span>{x.name}{x.preparation ? `, ${x.preparation}` : ""}</span></li>)}
            </ul>
          )}
        </div>
        <div>
          {photo ? (
            <button onClick={() => setZoom(true)} style={{ border: 0, padding: 0, background: "none", width: "100%", cursor: "zoom-in" }} aria-label="Open photo full screen">
              <Media src={photo.url} alt={stepPhoto ? `Step ${i + 1}` : "The finished dish"} ratio="ratio-hero" focal={[photo.focal_x ?? 0.5, photo.focal_y ?? 0.5]} credit={photo.attribution} illustration={photo.is_ai_generated} />
            </button>
          ) : null}
          {photo && !stepPhoto && <p className="xsmall muted" style={{ marginTop: 6 }}>The finished dish. Add a photo of this step from the recipe page.</p>}
          {!showIngs && (
            <ul className="ing-list cook-ings" style={{ marginTop: photo ? 16 : 0 }}>
              <li style={{ display: "block", borderBottom: 0 }} className="eyebrow">For {fmtServings(s)} servings</li>
              {v.ingredients.map((x: any) => <li key={x.id}><span className="amt">{x.unresolved ? "—" : x.display}</span><span>{x.name}</span></li>)}
            </ul>
          )}
        </div>
      </div>

      <div className="cook-bottom">
        <button className="btn" onClick={() => setI(Math.max(0, i - 1))} disabled={i === 0}><Icon name="left" />Previous</button>
        {i < last ? <button className="btn primary" onClick={() => setI(i + 1)}>Next<Icon name="right" /></button>
          : <button className="btn primary" onClick={() => setFinish(true)}><Icon name="check" />Finish</button>}
      </div>

      {zoom && photo && (
        <div className="lightbox" role="dialog" aria-label="Photo" onClick={() => setZoom(false)}>
          <img src={photo.url} alt="" />
          <button className="btn" onClick={() => setZoom(false)}><Icon name="close" />Back to step</button>
        </div>
      )}
      <FinishSheet open={finish} onClose={() => setFinish(false)} recipe={r} version={v} servings={s} planId={params.get("plan")} />
    </div>
  );
}

function FinishSheet({ open, onClose, recipe, version, servings, planId }: { open: boolean; onClose: () => void; recipe: any; version: any; servings: number; planId: string | null }) {
  const people = useApi<any[]>(open ? "/people" : null, [open]);
  const review = useApi<any>(open ? `/recipes/${recipe.id}/versions/${version.id}/review?servings=${servings}` : null, [open]);
  const toast = useToast();
  const nav = useNavigate();
  const [made, setMade] = useState(servings);
  const [portions, setPortions] = useState<Record<string, number>>({});
  const [left, setLeft] = useState(0);
  const [waste, setWaste] = useState(0);
  const [leftLoc, setLeftLoc] = useState("fridge");
  const [useBy, setUseBy] = useState("");
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [skip, setSkip] = useState<Record<number, boolean>>({});
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(uid());
  const eaten = Object.values(portions).reduce((a, b) => a + b, 0);
  const accounted = eaten + left + waste;
  const req: Record<number, any> = {};
  (review.data?.requirements || []).forEach((x: any) => (req[x.position] = x));
  const factor = made / version.default_servings;

  async function save() {
    setError(null);
    try {
      const deductions = version.ingredients.map((x: any) => {
        const base = x.quantity === null ? null : (x.quantity / (servings / version.default_servings)) * factor;
        const typed = amounts[x.position];
        return { ingredient_position: x.position, quantity: typed !== undefined && typed !== "" ? parseFloat(typed) : base, unit: x.unit, skip: !!skip[x.position] || x.optional };
      });
      const body = { recipe_version_id: version.id, meal_plan_id: planId, servings_made: made, deductions,
        portions: Object.entries(portions).filter(([, v]) => v > 0).map(([person_id, s]) => ({ person_id, servings: s })),
        leftover_servings: left, leftover_location: leftLoc, leftover_use_by: useBy || null, waste_servings: waste, idempotency_key: key.current };
      setResult(await api("/meals/cook", { body }));
    } catch (e) { setError(errorText(e)); }
  }

  if (result) {
    const used = result.batch.ingredients_used;
    return (
      <Sheet open={open} onClose={() => nav(`/recipes/${recipe.id}`)} title="Saved">
        <div className="stack">
          <Notice tone="ok">Recorded {fmtServings(result.batch.servings_made)} servings{result.batch.leftover_servings ? `, with ${fmtServings(result.batch.leftover_servings)} left over in the ${leftLoc}` : ""}.</Notice>
          <div>
            <h3>Pantry</h3>
            <ul className="list small" style={{ marginTop: 8 }}>
              {used.filter((u: any) => !u.skip).map((u: any, i: number) => (
                <li key={i} className="list-row" style={{ minHeight: 0, padding: "8px 0" }}>
                  <span className="grow">{u.name}</span>
                  <span className="num">{u.deducted?.length ? `−${Math.round(u.deducted.reduce((a: number, d: any) => a + d.quantity_base, 0) * 10) / 10} ${u.base_unit}` : ""}</span>
                  {u.shortfall && <span className="xsmall" style={{ color: "var(--ochre-ink)" }}>{typeof u.shortfall === "number" ? `${u.shortfall} short — stock not taken below zero` : u.shortfall}</span>}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Eaten</h3>
            <ul className="list small" style={{ marginTop: 8 }}>
              {result.consumptions.map((c: any) => {
                const p = (people.data || []).find((x) => x.id === c.person_id);
                const kcal = c.nutrition.energy_kcal;
                return <li key={c.id} className="list-row" style={{ minHeight: 0, padding: "8px 0" }}><span className="grow">{p?.name}</span><span className="num">{fmtServings(c.amount)} serving{c.amount === 1 ? "" : "s"}</span><span className="num muted">{kcal === null ? "kcal unknown" : `${c.provenance.status.energy_kcal === "partial" ? "≥" : ""}${Math.round(kcal)} kcal`}</span></li>;
              })}
            </ul>
            {result.unaccounted_servings > 0 && <p className="xsmall muted">{fmtServings(result.unaccounted_servings)} servings weren't assigned to anyone.</p>}
          </div>
          <button className="btn primary big" onClick={() => { toast("Nice. Enjoy your meal."); nav(`/recipes/${recipe.id}`); }}>Done</button>
        </div>
      </Sheet>
    );
  }

  return (
    <Sheet open={open} onClose={onClose} title="Finish cooking" wide>
      <div className="stack">
        <Field label="Servings made"><Stepper label="servings made" value={made} step={0.5} min={0.5} onChange={setMade} format={fmtServings} /></Field>
        <div>
          <h3>Who ate how much?</h3>
          <p className="xsmall muted">Set each person's actual portion; nothing is split evenly for you.</p>
          <ul className="list" style={{ marginTop: 8 }}>
            {(people.data || []).map((p) => (
              <li key={p.id} className="list-row" style={{ minHeight: 56 }}>
                <span className="grow"><span className="title">{p.name}</span> {p.kind === "child" && <span className="tag plain">child</span>}</span>
                <div className="row-wrap">
                  {[0.25, 0.5, 1].map((q) => <button key={q} className="chip" aria-pressed={portions[p.id] === q} onClick={() => setPortions({ ...portions, [p.id]: q })}>{fmtServings(q)}</button>)}
                  <Stepper label={`${p.name}'s servings`} value={portions[p.id] || 0} step={0.25} min={0} max={10} onChange={(n) => setPortions({ ...portions, [p.id]: n })} format={fmtServings} />
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="grid-2">
          <Field label="Leftovers (servings)"><Stepper label="leftover servings" value={left} step={0.5} min={0} onChange={setLeft} format={fmtServings} /></Field>
          <Field label="Thrown away (servings)"><Stepper label="wasted servings" value={waste} step={0.25} min={0} onChange={setWaste} format={fmtServings} /></Field>
        </div>
        {left > 0 && (
          <div className="grid-2">
            <Field label="Leftovers go in"><select className="input" value={leftLoc} onChange={(e) => setLeftLoc(e.target.value)}><option value="fridge">Fridge</option><option value="freezer">Freezer</option></select></Field>
            <Field label="Your use-by date" hint="Optional; the app doesn't decide food safety."><input className="input" type="date" value={useBy} onChange={(e) => setUseBy(e.target.value)} /></Field>
          </div>
        )}
        <p className={`small ${accounted > made ? "" : "muted"}`} style={{ color: accounted > made ? "var(--tomato-ink)" : undefined }}>
          {fmtServings(eaten)} eaten + {fmtServings(left)} left + {fmtServings(waste)} wasted = {fmtServings(accounted)} of {fmtServings(made)}
        </p>
        <details>
          <summary className="btn ghost small">Ingredients actually used</summary>
          <ul className="list" style={{ marginTop: 8 }}>
            {version.ingredients.map((x: any) => {
              const st = req[x.position];
              const base = x.quantity === null ? null : (x.quantity / (servings / version.default_servings)) * factor;
              return (
                <li key={x.id} className="list-row" style={{ minHeight: 52 }}>
                  <span className="grow small">{x.name} <span className="muted xsmall">{st?.status === "ok" ? "in stock" : st?.status === "missing" ? "not in pantry" : st?.status === "short" ? `short ${st.missing_display}` : ""}</span></span>
                  <input className="input num" style={{ width: 90, minHeight: 36 }} type="number" step="any" min={0} aria-label={`${x.name} amount used`}
                    placeholder={base === null ? "?" : String(Math.round(base * 100) / 100)} value={amounts[x.position] ?? ""} onChange={(e) => setAmounts({ ...amounts, [x.position]: e.target.value })} />
                  <span className="small" style={{ width: 40 }}>{x.unit}</span>
                  <label className="xsmall row"><input type="checkbox" checked={!!skip[x.position]} onChange={(e) => setSkip({ ...skip, [x.position]: e.target.checked })} /> not used</label>
                </li>
              );
            })}
          </ul>
        </details>
        {error && <Notice tone="error">{error}</Notice>}
        <button className="btn primary big" disabled={accounted > made} onClick={save}>Save meal and update pantry</button>
      </div>
    </Sheet>
  );
}
