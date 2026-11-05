import { FormEvent, useEffect, useState } from "react";
import Icon from "../components/Icon";
import { Empty, ErrorNote, Field, Loading, Notice, Sheet, useApi, useToast } from "../components/ui";
import { api, errorText } from "../lib/api";
import { addDays, fmtDay, fmtTime, isoToday } from "../lib/format";

const ROWS: [string, string][] = [["energy_kcal", "Energy"], ["protein_g", "Protein"], ["carbohydrate_g", "Carbohydrate"], ["sugars_g", "Sugars"],
  ["fat_g", "Fat"], ["saturates_g", "Saturates"], ["fibre_g", "Fibre"], ["salt_g", "Salt"]];

function fmtVal(v: number | null, key: string) {
  if (v === null) return "—";
  return key === "energy_kcal" ? Math.round(v).toLocaleString("en-GB") : v >= 10 ? v.toFixed(0) : v.toFixed(1);
}

function DayChart({ days, from, to }: { days: any[]; from: string; to: string }) {
  const all: string[] = [];
  for (let d = from; d <= to; d = addDays(d, 1)) all.push(d);
  const byDate = Object.fromEntries(days.map((d) => [d.date, d.nutrients.energy_kcal]));
  const max = Math.max(1, ...days.map((d) => d.nutrients.energy_kcal.value || 0));
  const W = 640, H = 220, pad = 28, bw = (W - pad * 2) / all.length;
  return (
    <figure style={{ margin: 0 }}>
      <svg className="bar-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Energy per day in kcal">
        <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--herb)" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--paper)" strokeWidth="2" /></pattern></defs>
        <line x1={pad} x2={W - pad} y1={H - 30} y2={H - 30} stroke="var(--rule-strong)" />
        {all.map((d, i) => {
          const n = byDate[d];
          const v = n?.value || 0;
          const h = (v / max) * (H - 70);
          const x = pad + i * bw + bw * 0.18;
          return (
            <g key={d}>
              {n?.value !== null && n && <rect x={x} y={H - 30 - h} width={bw * 0.64} height={h} rx={3} fill={n.status === "partial" ? "url(#hatch)" : "var(--herb)"} />}
              <text x={x + bw * 0.32} y={H - 12} textAnchor="middle">{fmtDay(d).split(" ").slice(0, 2).join(" ")}</text>
              <text className="value" x={x + bw * 0.32} y={H - 36 - h} textAnchor="middle">{n?.value != null ? `${n.status === "partial" ? "≥" : ""}${Math.round(n.value)}` : n ? "no data" : ""}</text>
            </g>
          );
        })}
      </svg>
      <figcaption className="xsmall muted">Energy (kcal) per day. Hatched bars are “at least” totals: some foods eaten that day have no energy data.</figcaption>
    </figure>
  );
}

export default function Nutrition() {
  const people = useApi<any[]>("/people");
  const [pid, setPid] = useState<string | null>(null);
  const [to, setTo] = useState(isoToday());
  const from = addDays(to, -6);
  useEffect(() => { if (!pid && people.data?.length) setPid(people.data[0].id); }, [people.data, pid]);
  const intake = useApi<any>(pid ? `/people/${pid}/intake?from=${from}&to=${to}` : null, [pid, from, to]);
  const [log, setLog] = useState(false);
  const person = people.data?.find((p) => p.id === pid);
  const d = intake.data;
  const totals = (k: string) => {
    const vals = (d?.days || []).map((x: any) => x.nutrients[k]);
    const known = vals.filter((v: any) => v.value !== null);
    return { avg: known.length ? known.reduce((a: number, v: any) => a + v.value, 0) / known.length : null, partial: vals.some((v: any) => v.status !== "complete") };
  };

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Nutrition</div><h1>What we've eaten</h1></div>
        <div className="row-wrap">
          <button className="btn icon-btn" aria-label="Previous week" onClick={() => setTo(addDays(to, -7))}><Icon name="left" /></button>
          <span className="small num">{fmtDay(from)} – {fmtDay(to)}</span>
          <button className="btn icon-btn" aria-label="Next week" onClick={() => setTo(addDays(to, 7))} disabled={to >= isoToday()}><Icon name="right" /></button>
        </div>
      </div>
      <div className="row-wrap" style={{ marginBottom: 16 }}>
        {(people.data || []).map((p) => <button key={p.id} className="chip" aria-pressed={p.id === pid} onClick={() => setPid(p.id)}>{p.name}{p.kind === "child" ? " (child)" : ""}</button>)}
      </div>
      {person?.kind === "child" && <div style={{ marginBottom: 16 }}><Notice>For children, use these figures as a record of what was eaten, not as targets. See the <a href="https://www.nhs.uk/baby/weaning-and-feeding/foods-to-avoid-giving-babies-and-young-children/" target="_blank" rel="noreferrer">NHS guidance</a> on salt and preparation for young children.</Notice></div>}
      {intake.error && <ErrorNote error={intake.error} retry={intake.reload} />}
      {intake.loading && !d && <Loading lines={6} />}
      {d && (
        <div className="today-grid">
          <div className="stack">
            {d.days.length === 0 ? <Empty icon="chart" title="Nothing logged this week">Finish a cook from a recipe, or log a snack, to see it here.</Empty> : <DayChart days={d.days} from={from} to={to} />}
            <table className="ledger">
              <thead><tr><th>Day</th>{ROWS.slice(0, 4).map(([k, l]) => <th key={k} className="money">{l}</th>)}<th className="money">Salt</th></tr></thead>
              <tbody>{d.days.map((day: any) => (
                <tr key={day.date}><td>{fmtDay(day.date)}</td>
                  {[...ROWS.slice(0, 4), ["salt_g", "Salt"]].map(([k]) => { const n = day.nutrients[k]; return <td key={k} className="money">{n.status === "partial" ? "≥" : ""}{fmtVal(n.value, k)} <span className="xsmall muted">{n.value !== null ? n.unit : ""}</span></td>; })}
                </tr>))}
              </tbody>
            </table>
            <p className="xsmall muted">{d.note}</p>
          </div>
          <div className="stack">
            <div className="panel">
              <div className="eyebrow" style={{ marginBottom: 8 }}>Daily average, days with entries</div>
              <dl className="facts small" style={{ borderTop: 0, borderBottom: 0, padding: 0 }}>
                {ROWS.map(([k, l]) => { const t = totals(k); return <div key={k}><dt>{l}</dt><dd>{t.avg === null ? "—" : `${t.partial ? "≥" : ""}${fmtVal(t.avg, k)}`}</dd></div>; })}
              </dl>
            </div>
            <div className="spread"><h2 style={{ fontSize: "1.1875rem" }}>Entries</h2><button className="btn small" onClick={() => setLog(true)}><Icon name="plus" size={16} />Log food</button></div>
            <ul className="list">
              {d.entries.slice().reverse().map((e: any) => (
                <li key={e.id} className="list-row">
                  <span className="grow"><span className="title">{e.description}</span><span className="meta" style={{ display: "block" }}>{fmtDay(e.consumed_at)} {fmtTime(e.consumed_at)} · {e.amount} {e.amount_unit}{e.amount === 1 || e.amount_unit !== "serving" ? "" : "s"}{e.nutrition.energy_kcal === null && !e.provenance.complete ? " · no nutrition data" : e.provenance.complete && !e.provenance.estimated ? " · from labels" : " · estimate"}</span></span>
                  <span className="qty">{e.nutrition.energy_kcal === null ? "?" : `${e.provenance.status?.energy_kcal === "partial" ? "≥" : ""}${Math.round(e.nutrition.energy_kcal)}`}<span className="xsmall muted"> kcal</span></span>
                </li>
              ))}
            </ul>
            {pid && (
              <div className="row-wrap">
                <a className="btn small" href={`/api/v1/people/${pid}/intake/export?format=csv&from=${from}&to=${to}`}><Icon name="download" size={16} />CSV</a>
                <a className="btn small" href={`/api/v1/people/${pid}/intake/export?format=json&from=${from}&to=${to}`} target="_blank" rel="noreferrer"><Icon name="download" size={16} />JSON</a>
                <span className="xsmall muted">For another app, create a read-only token in Settings.</span>
              </div>
            )}
          </div>
        </div>
      )}
      {pid && <LogSheet open={log} personId={pid} onClose={() => setLog(false)} onSaved={() => { setLog(false); intake.reload(); }} />}
    </>
  );
}

function LogSheet({ open, personId, onClose, onSaved }: { open: boolean; personId: string; onClose: () => void; onSaved: () => void }) {
  const [q, setQ] = useState("");
  const foods = useApi<any[]>(open && q.length > 1 ? `/foods?q=${encodeURIComponent(q)}&limit=8` : null, [q, open]);
  const [pick, setPick] = useState<any>(null);
  const [amount, setAmount] = useState("100");
  const [unit, setUnit] = useState("g");
  const [deduct, setDeduct] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/meals/consumption", { body: { person_id: personId, food_id: pick.id, amount: parseFloat(amount), amount_unit: unit, deduct_from_stock: deduct } });
      toast("Logged"); setPick(null); setQ(""); onSaved();
    } catch (err) { setError(errorText(err)); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Log food">
      <form className="stack" onSubmit={save}>
        <Field label="Food"><input className="input" type="search" value={q} onChange={(e) => { setQ(e.target.value); setPick(null); }} placeholder="e.g. banana" /></Field>
        {!pick && <ul className="list">{(foods.data || []).map((f) => <li key={f.id}><button type="button" className="list-row" style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }} onClick={() => { setPick(f); setQ(f.name); setUnit(f.default_unit === "each" ? "each" : f.default_unit); }}>{f.name}</button></li>)}</ul>}
        <div className="grid-2">
          <Field label="Amount"><input className="input num" type="number" min={0} step="any" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          <Field label="Unit"><select className="input" value={unit} onChange={(e) => setUnit(e.target.value)}>{["g", "ml", "each", "tbsp", "tsp"].map((u) => <option key={u}>{u}</option>)}</select></Field>
        </div>
        <label className="row small"><input type="checkbox" checked={deduct} onChange={(e) => setDeduct(e.target.checked)} /> Also take it out of the pantry</label>
        {error && <Notice tone="error">{error}</Notice>}
        <button className="btn primary big" disabled={!pick}>Log it</button>
      </form>
    </Sheet>
  );
}
