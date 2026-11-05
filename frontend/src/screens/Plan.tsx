import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import { ErrorNote, Field, Loading, Notice, Sheet, Stepper, useApi, useToast } from "../components/ui";
import { api, errorText } from "../lib/api";
import { addDays, fmtDay, isoToday, mondayOf, servings as fmtServings } from "../lib/format";

const SLOTS = ["breakfast", "lunch", "dinner", "snack"];
export const REASONS: Record<string, string> = { social: "Socialising", work: "Work", study: "Study", travel: "Away", other: "Out" };

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export function covers(a: any, d: string, slot: string) {
  if (d < a.start_date || (a.end_date && d > a.end_date)) return false;
  if (a.weekdays?.length) { const wd = (new Date(d + "T12:00:00").getDay() + 6) % 7; if (!a.weekdays.includes(wd)) return false; }
  return a.all_day || a.slots.includes(slot);
}
export function awayLabel(a: any) {
  const when = a.all_day ? "all day" : a.slots.join(", ");
  return `${a.person_name} · ${REASONS[a.reason] || a.reason} · ${when}${a.repeats ? ` (${a.repeats})` : ""}`;
}
const portionsFor = (ps: any[]) => Math.max(0.5, Math.round(ps.reduce((s, p) => s + (p.default_portion || 1), 0) * 2) / 2);

export default function Plan() {
  const [week, setWeek] = useState(mondayOf(isoToday()));
  const end = addDays(week, 6);
  const { data, error, loading, reload } = useApi<any>(`/plan?from=${week}&to=${end}`, [week]);
  const people = useApi<any[]>("/people");
  const [adding, setAdding] = useState<{ date: string; slot: string } | null>(null);
  const [editing, setEditing] = useState<any>(null);
  const [absence, setAbsence] = useState<any>(null);
  const toast = useToast();
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  const meals = (data?.meals || []) as any[];
  const absences = (data?.absences || []) as any[];
  const today = isoToday();

  async function planWithout(m: any) {
    try {
      const r = await api(`/plan/${m.id}/apply-absences`, { method: "POST" });
      toast(`${m.title}: planned without ${r.removed.join(" and ")}, now ${fmtServings(r.meal.servings)} servings. The shopping list has been updated.`);
      reload();
    } catch (e) { toast(errorText(e)); }
  }

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Plan</div><h1>Week of {fmtDay(week)}</h1></div>
        <div className="row-wrap">
          <button className="btn icon-btn" onClick={() => setWeek(addDays(week, -7))} aria-label="Previous week"><Icon name="left" /></button>
          <button className="btn" onClick={() => setWeek(mondayOf(isoToday()))}>This week</button>
          <button className="btn icon-btn" onClick={() => setWeek(addDays(week, 7))} aria-label="Next week"><Icon name="right" /></button>
          <button className="btn" onClick={() => setAbsence({ start_date: today < week ? week : today })}><Icon name="people" />Someone's out</button>
          <Link className="btn primary" to="/shopping"><Icon name="cart" />Shopping list</Link>
        </div>
      </div>
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={7} />}
      <ol className="list" style={{ listStyle: "none" }}>
        {days.map((d) => {
          const dm = meals.filter((m) => m.date === d);
          const away = (data?.away_by_date?.[d] || []) as any[];
          return (
            <li key={d} style={{ padding: "12px 0", background: d === today ? "color-mix(in srgb, var(--herb-tint) 45%, transparent)" : undefined }}>
              <div className="plan-day">
                <div className="day-name">
                  <div className="serif" style={{ fontSize: "1.1875rem" }}>{fmtDay(d).split(" ")[0]}</div>
                  <div className="small muted num">{fmtDay(d).split(" ").slice(1).join(" ")}{d === today ? " · today" : ""}</div>
                </div>
                <div className="stack-sm" style={{ minWidth: 0 }}>
                  {away.length > 0 && (
                    <div className="row-wrap">
                      {away.map((a) => (
                        <button key={a.id} className="away-tag"
                          onClick={() => setAbsence(a)} aria-label={`${awayLabel(a)}. Edit`}>
                          <Icon name="people" size={14} />{awayLabel(a)}{a.note ? ` — ${a.note}` : ""}
                        </button>
                      ))}
                    </div>
                  )}
                  {dm.sort((a, b) => SLOTS.indexOf(a.slot) - SLOTS.indexOf(b.slot)).map((m) => (
                    <div key={m.id}>
                      <div className="row" style={{ gap: 12 }}>
                        {m.hero ? <img className="thumb" src={m.hero.url} alt="" style={{ objectPosition: `${m.hero.focal_x * 100}% ${m.hero.focal_y * 100}%` }} /> : <span className="thumb placeholder"><Icon name="pot" size={20} /></span>}
                        <button onClick={() => setEditing(m)} style={{ all: "unset", cursor: "pointer", flex: 1, minWidth: 0 }}>
                          <div className="title" style={{ fontWeight: 600 }}>{m.title}</div>
                          <div className="meta small muted">{m.slot} · {fmtServings(m.servings)} servings · {m.participant_ids.length} eating{m.status !== "planned" ? ` · ${m.status}` : ""}</div>
                        </button>
                        {m.recipe_id && m.status === "planned" && <Link className="btn small" to={`/recipes/${m.recipe_id}/cook?servings=${m.servings}&plan=${m.id}&v=${m.recipe_version_id}`}>Cook</Link>}
                      </div>
                      {m.away_conflicts.length > 0 && (
                        <div className="row-wrap small" style={{ margin: "6px 0 0", paddingLeft: 56, color: "var(--ochre-ink)" }}>
                          <span>{m.away_conflicts.map((a: any) => a.person_name).join(" and ")} {m.away_conflicts.length > 1 ? "are" : "is"} out for this meal.</span>
                          <button className="btn small" onClick={() => planWithout(m)}>Plan without {m.away_conflicts.length > 1 ? "them" : m.away_conflicts[0].person_name}</button>
                        </div>
                      )}
                    </div>
                  ))}
                  <div><button className="btn ghost small" onClick={() => setAdding({ date: d, slot: dm.some((m) => m.slot === "dinner") ? "lunch" : "dinner" })}><Icon name="plus" size={18} />Add meal</button></div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="xsmall muted" style={{ marginTop: 12 }}>Marking someone as out doesn't change meals you've already planned until you choose “Plan without”. New meals start with whoever is home.</p>
      <AddMeal open={!!adding} at={adding} people={people.data || []} absences={absences} onClose={() => setAdding(null)} onSaved={() => { setAdding(null); reload(); }} />
      <EditMeal meal={editing} people={people.data || []} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />
      <AbsenceSheet value={absence} people={people.data || []} onClose={() => setAbsence(null)} onSaved={() => { setAbsence(null); reload(); }} />
    </>
  );
}

export function AbsenceSheet({ value, people, onClose, onSaved }: { value: any; people: any[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>(null);
  const [affected, setAffected] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  useEffect(() => {
    if (value) setF({ person_id: value.person_id || people[0]?.id, start_date: value.start_date, end_date: value.end_date || (value.weekdays?.length ? "" : value.start_date),
      slots: value.slots || ["dinner"], reason: value.reason || "social", note: value.note || "", weekdays: value.weekdays || [] });
    setAffected(null); setError(null);
  }, [value, people]);
  if (!value || !f) return null;
  const person = people.find((p) => p.id === f.person_id);
  const allDay = f.slots.length === 0;
  const toggleSlot = (s: string) => setF({ ...f, slots: f.slots.includes(s) ? f.slots.filter((x: string) => x !== s) : [...f.slots, s] });

  async function save() {
    setError(null);
    try {
      const body = { ...f, end_date: f.end_date || (f.weekdays.length ? null : f.start_date), note: f.note || null };
      const r = value.id ? await api(`/absences/${value.id}`, { method: "PATCH", body }) : await api("/absences", { body });
      if (r.affected_meals.length) setAffected(r.affected_meals);
      else { toast(`${person?.name} marked as out`); onSaved(); }
    } catch (e) { setError(errorText(e)); }
  }
  async function applyAll() {
    for (const m of affected || []) await api(`/plan/${m.id}/apply-absences`, { method: "POST" });
    toast("Meals updated; the shopping list follows"); onSaved();
  }
  async function remove() { await api(`/absences/${value.id}`, { method: "DELETE" }); toast("Removed"); onSaved(); }

  return (
    <Sheet open onClose={onClose} title={value.id ? "Change who's out" : "Someone's out"}>
      {affected ? (
        <div className="stack">
          <Notice tone="warn">{person?.name} is down to eat at {affected.length === 1 ? "a meal" : `${affected.length} meals`} you've already planned:</Notice>
          <ul className="list small">{affected.map((m) => <li key={m.id} className="list-row" style={{ minHeight: 44 }}><span className="grow">{fmtDay(m.date)} · {m.slot} · {m.title}</span><span className="num muted">{fmtServings(m.servings)} servings</span></li>)}</ul>
          <div className="row-wrap">
            <button className="btn primary" onClick={applyAll}>Plan {affected.length === 1 ? "it" : "them"} without {person?.name}</button>
            <button className="btn" onClick={onSaved}>Leave as planned</button>
          </div>
          <p className="xsmall muted">Servings go down by {person?.name}'s usual portion ({fmtServings(person?.default_portion || 1)}).</p>
        </div>
      ) : (
        <div className="stack">
          <div>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Who</div>
            <div className="row-wrap">{people.map((p) => <button key={p.id} className="chip" aria-pressed={p.id === f.person_id} onClick={() => setF({ ...f, person_id: p.id })}>{p.name}</button>)}</div>
          </div>
          <div>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Repeats every week on (optional)</div>
            <div className="row-wrap">{WEEKDAYS.map((w, i) => { const on = f.weekdays.includes(i); return <button key={w} className="chip" aria-pressed={on} onClick={() => setF({ ...f, weekdays: on ? f.weekdays.filter((x: number) => x !== i) : [...f.weekdays, i], end_date: !on && f.end_date === f.start_date ? "" : f.end_date })}>{w}</button>; })}</div>
          </div>
          <div className="grid-2">
            <Field label={f.weekdays.length ? "Starting" : "From"}><input className="input" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value, end_date: f.end_date && f.end_date < e.target.value ? e.target.value : f.end_date })} /></Field>
            <Field label={f.weekdays.length ? "Until (leave empty to keep going)" : "Until (inclusive)"}><input className="input" type="date" min={f.start_date} value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
          </div>
          <div>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Missing</div>
            <div className="row-wrap">
              <button className="chip" aria-pressed={allDay} onClick={() => setF({ ...f, slots: [] })}>All meals</button>
              {SLOTS.filter((s) => s !== "snack").map((s) => <button key={s} className="chip" aria-pressed={!allDay && f.slots.includes(s)} onClick={() => toggleSlot(s)}>{s}</button>)}
            </div>
          </div>
          <div>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Why</div>
            <div className="row-wrap">{Object.entries(REASONS).map(([k, l]) => <button key={k} className="chip" aria-pressed={f.reason === k} onClick={() => setF({ ...f, reason: k })}>{k === "other" ? "Other" : l}</button>)}</div>
          </div>
          <Field label="Note (optional)"><input className="input" maxLength={200} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. team dinner, evening class" /></Field>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="row-wrap">
            <button className="btn primary" onClick={save}>Save</button>
            {value.id && <button className="btn danger" onClick={remove}>Remove</button>}
          </div>
        </div>
      )}
    </Sheet>
  );
}

function AddMeal({ open, at, people, absences, onClose, onSaved }: { open: boolean; at: { date: string; slot: string } | null; people: any[]; absences: any[]; onClose: () => void; onSaved: () => void }) {
  const [q, setQ] = useState("");
  const recipes = useApi<any[]>(open ? `/recipes?q=${encodeURIComponent(q)}` : null, [open, q]);
  const [pick, setPick] = useState<any>(null);
  const [slot, setSlot] = useState("dinner");
  const [who, setWho] = useState<string[]>([]);
  const [serv, setServ] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const toast = useToast();
  const away = at ? absences.filter((a) => covers(a, at.date, slot)) : [];
  useEffect(() => { if (at) setSlot(at.slot); }, [at]);
  useEffect(() => {
    if (!at) return;
    const awayIds = new Set(absences.filter((a) => covers(a, at.date, slot)).map((a) => a.person_id));
    setWho(people.filter((p) => !awayIds.has(p.id)).map((p) => p.id));
    setServ(null);
  }, [at, slot, people, absences]);
  const s = serv ?? portionsFor(people.filter((p) => who.includes(p.id)));
  async function save() {
    try {
      await api("/plan", { body: { date: at!.date, slot, recipe_id: pick?.id || null, title: pick ? null : title, servings: s, participant_ids: who } });
      toast("Planned"); setPick(null); setTitle(""); onSaved();
    } catch (e) { toast(errorText(e)); }
  }
  return (
    <Sheet open={open} onClose={onClose} title={at ? `Add a meal · ${fmtDay(at.date)}` : "Add a meal"}>
      <div className="stack">
        <Field label="Meal"><select className="input" value={slot} onChange={(e) => setSlot(e.target.value)}>{SLOTS.map((x) => <option key={x}>{x}</option>)}</select></Field>
        {away.length > 0 && <Notice>{away.map((a) => `${a.person_name} is out (${(REASONS[a.reason] || a.reason).toLowerCase()})`).join("; ")}, so they're not included.</Notice>}
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Who's eating</div>
          <div className="row-wrap">{people.map((p) => { const on = who.includes(p.id); return <button key={p.id} className="chip" aria-pressed={on} onClick={() => { setWho(on ? who.filter((x) => x !== p.id) : [...who, p.id]); setServ(null); }}>{p.name}</button>; })}</div>
        </div>
        <Field label="Recipe"><input className="input" type="search" placeholder="Search recipes" value={q} onChange={(e) => setQ(e.target.value)} /></Field>
        <ul className="list" style={{ maxHeight: 240, overflow: "auto" }}>
          {(recipes.data || []).map((r) => (
            <li key={r.id}><button className="list-row" onClick={() => setPick(r)} style={{ width: "100%", border: 0, background: pick?.id === r.id ? "var(--herb-tint)" : "transparent", textAlign: "left", cursor: "pointer", padding: "8px" }} aria-pressed={pick?.id === r.id}>
              <span className="grow"><span className="title">{r.title}</span><span className="meta" style={{ display: "block" }}>serves {r.default_servings}{r.match?.missing?.length ? ` · missing ${r.match.missing.length}` : ""}</span></span>
              {pick?.id === r.id && <Icon name="check" />}
            </button></li>
          ))}
        </ul>
        {!pick && <Field label="…or just a name" hint="e.g. “Leftovers”, “Takeaway”"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>}
        <Field label="Servings to cook" hint={serv === null ? "From each person's usual portion" : undefined}><Stepper label="servings" value={s} step={0.5} min={0.5} onChange={setServ} format={fmtServings} /></Field>
        <button className="btn primary big" disabled={!pick && !title} onClick={save}>Add to plan</button>
      </div>
    </Sheet>
  );
}

function EditMeal({ meal, people, onClose, onSaved }: { meal: any; people: any[]; onClose: () => void; onSaved: () => void }) {
  const [serv, setServ] = useState<number | null>(null);
  const [who, setWho] = useState<string[] | null>(null);
  const toast = useToast();
  if (!meal) return null;
  const s = serv ?? meal.servings;
  const ids = who ?? meal.participant_ids;
  async function save() { try { await api(`/plan/${meal.id}`, { method: "PATCH", body: { servings: s, participant_ids: ids } }); toast("Updated; the shopping list follows"); setServ(null); setWho(null); onSaved(); } catch (e) { toast(errorText(e)); } }
  async function remove() { await api(`/plan/${meal.id}`, { method: "DELETE" }); setServ(null); setWho(null); onSaved(); }
  return (
    <Sheet open={!!meal} onClose={() => { setServ(null); setWho(null); onClose(); }} title={meal.title}>
      <div className="stack">
        <p className="muted small">{fmtDay(meal.date)} · {meal.slot}{meal.version_label ? ` · ${meal.version_label}` : ""}</p>
        {meal.away_conflicts.length > 0 && <Notice tone="warn">{meal.away_conflicts.map((a: any) => awayLabel(a)).join("; ")}</Notice>}
        <Field label="Servings to cook"><Stepper label="servings" value={s} step={0.5} min={0.5} onChange={setServ} format={fmtServings} /></Field>
        <div><div className="eyebrow" style={{ marginBottom: 8 }}>Who's eating</div>
          <div className="row-wrap">{people.map((p) => { const on = ids.includes(p.id); return <button key={p.id} className="chip" aria-pressed={on} onClick={() => setWho(on ? ids.filter((x: string) => x !== p.id) : [...ids, p.id])}>{p.name}</button>; })}</div></div>
        <div className="row-wrap">
          <button className="btn primary" onClick={save}>Save</button>
          {meal.recipe_id && <Link className="btn" to={`/recipes/${meal.recipe_id}?servings=${s}&v=${meal.recipe_version_id}`}>Open recipe</Link>}
          <button className="btn danger" onClick={remove}>Remove</button>
        </div>
      </div>
    </Sheet>
  );
}
