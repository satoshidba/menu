import { useEffect, useState } from "react";
import Icon from "../components/Icon";
import { DateBadge, Empty, ErrorNote, Field, Loading, Notice, Segmented, Sheet, useApi, useToast } from "../components/ui";
import { api, errorText, uid } from "../lib/api";
import { fmtTime } from "../lib/format";

const NUTRIENT_FIELDS: [string, string][] = [
  ["vitamin_d_ug", "Vitamin D (µg)"], ["vitamin_c_mg", "Vitamin C (mg)"], ["vitamin_a_ug", "Vitamin A (µg)"], ["iron_mg", "Iron (mg)"],
  ["folate_ug", "Folic acid (µg)"], ["vitamin_b12_ug", "Vitamin B12 (µg)"], ["calcium_mg", "Calcium (mg)"], ["zinc_mg", "Zinc (mg)"],
  ["iodine_ug", "Iodine (µg)"], ["omega3_mg", "Omega-3 (mg)"], ["protein_g", "Protein (g)"], ["energy_kcal", "Energy (kcal)"],
  ["carbohydrate_g", "Carbohydrate (g)"], ["sugars_g", "Sugars (g)"], ["fat_g", "Fat (g)"], ["salt_g", "Salt (g)"],
];
const KIND_LABEL: Record<string, string> = { supplement: "Vitamin / supplement", medicine: "Medicine" };

/** Give a dose; if it conflicts with the entered directions, show the reasons and require an explicit second tap. */
export function useGiveDose(onDone: () => void) {
  const toast = useToast();
  const [pending, setPending] = useState<{ body: any; warnings: any[]; advice: string } | null>(null);
  async function give(body: any, acknowledge = false) {
    try {
      const r = await api("/health/doses", { body: { ...body, acknowledge_warnings: acknowledge }, idempotencyKey: uid() });
      setPending(null);
      const notes = (r.notes || []).filter((n: any) => n.code === "stock").map((n: any) => n.message);
      toast(`Recorded: ${r.dose.item} for ${r.dose.person}${notes.length ? `. ${notes[0]}` : ""}`);
      onDone();
    } catch (e: any) {
      if (e?.detail?.code === "check_label") setPending({ body, warnings: e.detail.warnings, advice: e.detail.advice });
      else toast(errorText(e));
    }
  }
  const sheet = (
    <Sheet open={!!pending} onClose={() => setPending(null)} title="Check before giving">
      {pending && (
        <div className="stack">
          {pending.warnings.map((w, i) => <Notice key={i} tone={w.code === "no_directions" ? "warn" : "error"}>{w.message}</Notice>)}
          <p className="small muted">{pending.advice}</p>
          <div className="row-wrap">
            <button className="btn" onClick={() => setPending(null)}>Don't record</button>
            <button className="btn danger" onClick={() => give(pending.body, true)}>Record anyway</button>
          </div>
        </div>
      )}
    </Sheet>
  );
  return { give, sheet };
}

export default function Health() {
  const cab = useApi<any>("/health/cabinet");
  const today = useApi<any>("/health/today");
  const doses = useApi<any>("/health/doses");
  const people = useApi<any[]>("/people");
  const [kind, setKind] = useState<"all" | "supplement" | "medicine">("all");
  const [adding, setAdding] = useState(false);
  const [logging, setLogging] = useState<any>(null);
  const [open, setOpen] = useState<any>(null);
  const reload = () => { cab.reload(); today.reload(); doses.reload(); };
  const { give, sheet } = useGiveDose(reload);
  const toast = useToast();
  const items = ((cab.data?.items || []) as any[]).filter((i) => kind === "all" || i.kind === kind);
  const due = (today.data?.due || []) as any[];

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Household</div><h1>Medicines &amp; vitamins</h1></div>
        <div className="row-wrap">
          <button className="btn" onClick={() => setLogging({})}><Icon name="check" />Log a dose</button>
          <button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" />Add item</button>
        </div>
      </div>
      <p className="small muted" style={{ maxWidth: 720, marginTop: -8 }}>
        Record what's on the label or what your GP, pharmacist or health visitor told you. Larder reminds you and checks new doses
        against those directions; it never works out doses. If unsure, ask a pharmacist or call NHS 111.
      </p>
      {(cab.error || today.error) && <ErrorNote error={(cab.error || today.error)!} retry={reload} />}

      <div className="today-grid" style={{ marginTop: 16 }}>
        <div>
          <div className="section-title" style={{ marginTop: 0 }}><h2>Due today</h2></div>
          {today.loading && !today.data ? <Loading /> : due.length === 0 ? (
            <p className="muted small">No regular doses set up. Open an item and add who takes it, e.g. “Mia: vitamin D drops, daily”.</p>
          ) : (
            <ul className="list">
              {due.map((d) => (
                <li key={d.regimen_id} className="list-row">
                  <span className="grow">
                    <span className="title">{d.person} · {d.item}</span>
                    <span className="meta" style={{ display: "block" }}>
                      {d.dose}{d.as_needed ? " · when needed" : ` · ${d.given_today} of ${d.times_per_day} today`}
                      {d.given_times.length > 0 && ` (given ${d.given_times.join(", ")})`}
                      {d.next_allowed_at && ` · next from ${fmtTime(d.next_allowed_at)}`}
                    </span>
                    {d.instructions && <span className="xsmall muted" style={{ display: "block" }}>{d.instructions}{d.source ? ` (${d.source})` : ""}</span>}
                  </span>
                  {d.remaining_today > 0 || d.as_needed
                    ? <button className={`btn small ${d.remaining_today > 0 ? "primary" : ""}`} onClick={() => give({ person_id: d.person_id, regimen_id: d.regimen_id })}>Given</button>
                    : <span className="tag herb"><Icon name="check" size={14} />Done</span>}
                </li>
              ))}
            </ul>
          )}

          <div className="section-title"><h2>Cabinet</h2>
            <Segmented label="Show" value={kind} onChange={setKind} options={[{ value: "all", label: "All" }, { value: "supplement", label: "Vitamins" }, { value: "medicine", label: "Medicines" }]} />
          </div>
          {cab.loading && !cab.data ? <Loading lines={4} /> : items.length === 0 ? (
            <Empty icon="jar" title="Nothing in the cabinet yet">Add vitamins (like the children's vitamin drops), protein powder or medicines to keep track of stock, expiry dates and doses.</Empty>
          ) : (
            <ul className="list">
              {items.map((i) => (
                <li key={i.product_id}>
                  <button className="list-row" onClick={() => setOpen(i)} style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }}>
                    <span className="thumb placeholder"><Icon name={i.kind === "medicine" ? "pill" : "leaf"} size={20} /></span>
                    <span className="grow">
                      <span className="title" style={{ display: "block" }}>{i.brand ? `${i.brand} ` : ""}{i.name}</span>
                      <span className="meta">{KIND_LABEL[i.kind]}{i.dose ? ` · dose ${i.dose}` : ""}{i.label_min_interval_hours ? ` · ≥${i.label_min_interval_hours} h apart` : ""}{i.label_max_doses_24h ? ` · max ${i.label_max_doses_24h}/24 h` : ""}</span>
                      <span style={{ display: "block" }}>
                        {i.earliest_expiry && <DateBadge kind="use_by" date={i.earliest_expiry} label="Expires" />}
                        {i.low_stock && <span className="tag ochre" style={{ marginLeft: 8 }}>Low</span>}
                      </span>
                    </span>
                    <span className="qty">{i.stock || "none"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="section-title" style={{ marginTop: 0 }}><h2>Recent doses</h2></div>
          {(doses.data?.doses || []).length === 0 ? <p className="muted small">No doses recorded this week.</p> : (
            <ul className="list">
              {doses.data.doses.slice(0, 25).map((d: any) => (
                <li key={d.dose_id} className="list-row" style={{ minHeight: 52 }}>
                  <span className="grow">
                    <span className="title">{d.person} · {d.item}</span>
                    <span className="meta" style={{ display: "block" }}>{d.local_time} · {d.amount} {d.unit}{d.counts_in_nutrition ? " · counted in nutrition" : ""}</span>
                    {d.warnings_acknowledged?.length > 0 && <span className="xsmall" style={{ color: "var(--tomato-ink)", display: "block" }}>Recorded despite: {d.warnings_acknowledged.map((w: any) => w.code === "interval" ? "too soon" : w.code === "max_24h" ? "over the 24 h maximum" : w.code).join(", ")}</span>}
                  </span>
                  <button className="btn ghost small" aria-label="Remove this dose" onClick={async () => { await api(`/health/doses/${d.dose_id}`, { method: "DELETE" }); toast("Dose removed"); reload(); }}><Icon name="trash" size={16} /></button>
                </li>
              ))}
            </ul>
          )}
          {(today.data?.attention || []).length > 0 && (
            <>
              <div className="section-title"><h2>Needs attention</h2></div>
              <ul className="list small">
                {today.data.attention.map((a: any, k: number) => (
                  <li key={k} className="list-row" style={{ minHeight: 44 }}>
                    <span className="grow">{a.item}</span>
                    {a.expiry && <DateBadge kind="use_by" date={a.expiry} label="Expires" />}
                    {a.low_stock && <span className="tag ochre">Low ({a.stock})</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>

      <AddItem open={adding} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />
      <LogDose value={logging} items={cab.data?.items || []} people={people.data || []} onClose={() => setLogging(null)} give={(b) => { setLogging(null); give(b); }} />
      <ItemSheet item={open} people={people.data || []} onClose={() => setOpen(null)} onChanged={reload} onLog={(i) => { setOpen(null); setLogging({ product_id: i.product_id }); }} />
      {sheet}
    </>
  );
}

function AddItem({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const blank = { kind: "supplement", name: "", brand: "", pack_quantity: "", pack_unit: "tablet", dose_amount: "1", dose_unit: "tablet",
    label_min_interval_hours: "", label_max_doses_24h: "", active_ingredients: "", label_directions: "", stock_quantity: "1", expiry: "", min_stock_base: "" };
  const [f, setF] = useState<any>(blank);
  const [nut, setNut] = useState<Record<string, string>>({});
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const num = (v: string) => (v === "" ? null : parseFloat(v));
  async function save() {
    setError(null);
    const nutrients = Object.fromEntries(Object.entries(nut).filter(([, v]) => v !== "").map(([k, v]) => [k, parseFloat(v)]));
    try {
      await api("/health/items", { body: {
        kind: f.kind, name: f.name, brand: f.brand || null, pack_quantity: num(f.pack_quantity), pack_unit: f.pack_unit, dose_amount: num(f.dose_amount),
        dose_unit: f.dose_unit, label_min_interval_hours: num(f.label_min_interval_hours), label_max_doses_24h: num(f.label_max_doses_24h),
        active_ingredients: f.active_ingredients || null, label_directions: f.label_directions || null, stock_quantity: num(f.stock_quantity),
        expiry: f.expiry || null, min_stock_base: num(f.min_stock_base), nutrition_per_dose: f.kind === "supplement" && Object.keys(nutrients).length ? nutrients : null } });
      setF(blank); setNut({}); onSaved();
    } catch (e) { setError(errorText(e)); }
  }
  const units = ["tablet", "capsule", "gummy", "drop", "ml", "g", "scoop", "sachet", "spray"];
  return (
    <Sheet open={open} onClose={onClose} title="Add to the cabinet" wide>
      <div className="stack">
        <Segmented label="Type" value={f.kind} onChange={(v) => setF({ ...f, kind: v })} options={[{ value: "supplement", label: "Vitamin / supplement / protein" }, { value: "medicine", label: "Medicine" }]} />
        <div className="grid-2">
          <Field label="Name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={f.kind === "medicine" ? "e.g. Infant paracetamol suspension" : "e.g. Children's vitamin drops"} /></Field>
          <Field label="Brand"><input className="input" value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })} /></Field>
        </div>
        <div className="grid-2">
          <Field label="Pack contains"><div className="row"><input className="input num" type="number" min={0} step="any" value={f.pack_quantity} onChange={(e) => setF({ ...f, pack_quantity: e.target.value })} />
            <select className="input" style={{ width: 110 }} value={f.pack_unit} onChange={(e) => setF({ ...f, pack_unit: e.target.value })}>{[...units, "kg", "l"].map((u) => <option key={u}>{u}</option>)}</select></div></Field>
          <Field label="One dose (from the label)"><div className="row"><input className="input num" type="number" min={0} step="any" value={f.dose_amount} onChange={(e) => setF({ ...f, dose_amount: e.target.value })} />
            <select className="input" style={{ width: 110 }} value={f.dose_unit} onChange={(e) => setF({ ...f, dose_unit: e.target.value })}>{units.map((u) => <option key={u}>{u}</option>)}</select></div></Field>
        </div>
        {f.kind === "medicine" ? (
          <>
            <div className="grid-2">
              <Field label="At least … hours between doses" hint="As printed on the label or prescription"><input className="input num" type="number" min={0} step="0.5" value={f.label_min_interval_hours} onChange={(e) => setF({ ...f, label_min_interval_hours: e.target.value })} /></Field>
              <Field label="No more than … doses in 24 hours"><input className="input num" type="number" min={1} step="1" value={f.label_max_doses_24h} onChange={(e) => setF({ ...f, label_max_doses_24h: e.target.value })} /></Field>
            </div>
            <Field label="Active ingredients and strength"><input className="input" value={f.active_ingredients} onChange={(e) => setF({ ...f, active_ingredients: e.target.value })} placeholder="e.g. Paracetamol 120 mg/5 ml" /></Field>
            <Field label="Directions (copy from the packet)" hint="Age bands and doses vary; Larder doesn't interpret them."><textarea className="input" style={{ minHeight: 80 }} value={f.label_directions} onChange={(e) => setF({ ...f, label_directions: e.target.value })} /></Field>
          </>
        ) : (
          <div>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Per dose, from the label</div>
            <div className="grid-3">
              {NUTRIENT_FIELDS.slice(0, more ? NUTRIENT_FIELDS.length : 6).map(([k, l]) => (
                <Field key={k} label={l}><input className="input num" type="number" min={0} step="any" value={nut[k] || ""} onChange={(e) => setNut({ ...nut, [k]: e.target.value })} /></Field>
              ))}
            </div>
            <button className="btn ghost small" onClick={() => setMore(!more)} style={{ marginTop: 8 }}>{more ? "Fewer nutrients" : "More nutrients (protein, energy…)"}</button>
            <p className="xsmall muted">Only what you enter counts towards nutrition; anything left blank stays unknown.</p>
          </div>
        )}
        <div className="grid-3">
          <Field label="Packs you have now"><input className="input num" type="number" min={0} step="1" value={f.stock_quantity} onChange={(e) => setF({ ...f, stock_quantity: e.target.value })} /></Field>
          <Field label="Expiry"><input className="input" type="date" value={f.expiry} onChange={(e) => setF({ ...f, expiry: e.target.value })} /></Field>
          <Field label={`Remind me below (${f.pack_unit})`}><input className="input num" type="number" min={0} step="any" value={f.min_stock_base} onChange={(e) => setF({ ...f, min_stock_base: e.target.value })} /></Field>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <button className="btn primary big" disabled={!f.name} onClick={save}>Add</button>
      </div>
    </Sheet>
  );
}

function LogDose({ value, items, people, onClose, give }: { value: any; items: any[]; people: any[]; onClose: () => void; give: (body: any) => void }) {
  const [f, setF] = useState<any>({});
  const [check, setCheck] = useState<any>(null);
  useEffect(() => {
    if (value) { const it = items.find((i) => i.product_id === value.product_id) || items[0]; setF({ person_id: people[0]?.id, product_id: it?.product_id, amount: it?.dose_amount ?? 1, unit: it?.dose_unit || "dose", when: "", notes: "" }); }
  }, [value, items, people]);
  useEffect(() => {
    if (value && f.person_id && f.product_id) api(`/health/doses/check?person_id=${f.person_id}&product_id=${f.product_id}`).then(setCheck).catch(() => setCheck(null));
  }, [value, f.person_id, f.product_id]);
  if (!value) return null;
  const item = items.find((i) => i.product_id === f.product_id);
  return (
    <Sheet open onClose={onClose} title="Log a dose">
      <div className="stack">
        <div><div className="eyebrow" style={{ marginBottom: 8 }}>Who</div>
          <div className="row-wrap">{people.map((p) => <button key={p.id} className="chip" aria-pressed={f.person_id === p.id} onClick={() => setF({ ...f, person_id: p.id })}>{p.name}</button>)}</div></div>
        <Field label="What"><select className="input" value={f.product_id || ""} onChange={(e) => { const it = items.find((i) => i.product_id === e.target.value); setF({ ...f, product_id: e.target.value, amount: it?.dose_amount ?? 1, unit: it?.dose_unit || "dose" }); }}>
          {items.map((i) => <option key={i.product_id} value={i.product_id}>{i.brand ? `${i.brand} ` : ""}{i.name}</option>)}</select></Field>
        {item?.label_directions && <p className="small muted">Directions you recorded: {item.label_directions}</p>}
        <div className="grid-2">
          <Field label="Amount"><div className="row"><input className="input num" type="number" min={0} step="any" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /><span>{f.unit}</span></div></Field>
          <Field label="When" hint="Leave empty for now"><input className="input" type="datetime-local" value={f.when} onChange={(e) => setF({ ...f, when: e.target.value })} /></Field>
        </div>
        {check?.next_allowed_at && <Notice tone="warn">By the directions you entered, the next dose is from {fmtTime(check.next_allowed_at)}.</Notice>}
        {check?.warnings?.filter((w: any) => w.code === "no_directions").map((w: any, k: number) => <Notice key={k} tone="warn">{w.message}</Notice>)}
        <Field label="Notes"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <button className="btn primary big" disabled={!f.person_id || !f.product_id} onClick={() => give({
          person_id: f.person_id, product_id: f.product_id, amount: parseFloat(f.amount), unit: f.unit, notes: f.notes || null,
          given_at: f.when ? new Date(f.when).toISOString() : null })}>Record dose</button>
      </div>
    </Sheet>
  );
}

function ItemSheet({ item, people, onClose, onChanged, onLog }: { item: any; people: any[]; onClose: () => void; onChanged: () => void; onLog: (i: any) => void }) {
  const regs = useApi<any[]>(item ? "/health/regimens" : null, [item?.product_id]);
  const [r, setR] = useState<any>(null);
  const toast = useToast();
  if (!item) return null;
  const mine = (regs.data || []).filter((x) => x.product_id === item.product_id);
  async function addRegimen() {
    try {
      await api("/health/regimens", { body: { person_id: r.person_id, product_id: item.product_id, times_per_day: parseInt(r.times) || 1, as_needed: r.as_needed,
        dose_amount: r.dose ? parseFloat(r.dose) : null, end_date: r.end || null, instructions: r.instructions || null, source: r.source || null } });
      toast("Added"); setR(null); regs.reload(); onChanged();
    } catch (e) { toast(errorText(e)); }
  }
  return (
    <Sheet open onClose={onClose} title={`${item.brand ? item.brand + " " : ""}${item.name}`}>
      <div className="stack">
        <dl className="facts small" style={{ gap: 20 }}>
          <div><dt>Type</dt><dd>{KIND_LABEL[item.kind]}</dd></div>
          {item.dose && <div><dt>Dose</dt><dd>{item.dose}</dd></div>}
          <div><dt>In stock</dt><dd>{item.stock || "none"}</dd></div>
          {item.earliest_expiry && <div><dt>Expires</dt><dd>{item.earliest_expiry}</dd></div>}
        </dl>
        {item.active_ingredients && <p className="small">{item.active_ingredients}</p>}
        {(item.label_min_interval_hours || item.label_max_doses_24h) && <p className="small">Directions you entered: {item.label_min_interval_hours ? `at least ${item.label_min_interval_hours} h between doses` : ""}{item.label_min_interval_hours && item.label_max_doses_24h ? "; " : ""}{item.label_max_doses_24h ? `no more than ${item.label_max_doses_24h} in 24 h` : ""}.</p>}
        {item.label_directions && <p className="small muted">{item.label_directions}</p>}
        {item.nutrition_per_dose && Object.keys(item.nutrition_per_dose).length > 0 && <p className="small">Per dose: {Object.entries(item.nutrition_per_dose).map(([k, v]) => `${NUTRIENT_FIELDS.find((n) => n[0] === k)?.[1] || k} ${v}`).join(", ")}</p>}
        <div><button className="btn primary" onClick={() => onLog(item)}>Log a dose</button></div>
        <div>
          <h3>Who takes it</h3>
          {mine.length === 0 ? <p className="small muted">Nobody on a regular routine.</p> : (
            <ul className="list small" style={{ marginTop: 8 }}>
              {mine.map((x) => (
                <li key={x.regimen_id} className="list-row" style={{ minHeight: 48 }}>
                  <span className="grow"><strong>{x.person}</strong> · {x.dose_amount} {x.dose_unit} {x.as_needed ? "when needed" : `${x.times_per_day}× a day`}{x.end_date ? ` until ${x.end_date}` : ""}{x.source ? ` · ${x.source}` : ""}</span>
                  <button className="btn ghost small" onClick={async () => { await api(`/health/regimens/${x.regimen_id}`, { method: "DELETE" }); regs.reload(); onChanged(); }}>Stop</button>
                </li>
              ))}
            </ul>
          )}
          {!r ? <button className="btn small" style={{ marginTop: 8 }} onClick={() => setR({ person_id: people[0]?.id, times: "1", as_needed: item.kind === "medicine", dose: "", end: "", source: "", instructions: "" })}><Icon name="plus" size={16} />Add someone</button> : (
            <div className="panel stack-sm" style={{ marginTop: 8 }}>
              <div className="row-wrap">{people.map((p) => <button key={p.id} className="chip" aria-pressed={r.person_id === p.id} onClick={() => setR({ ...r, person_id: p.id })}>{p.name}</button>)}</div>
              <label className="row small"><input type="checkbox" checked={r.as_needed} onChange={(e) => setR({ ...r, as_needed: e.target.checked })} /> Only when needed (no reminders)</label>
              <div className="grid-3">
                {!r.as_needed && <Field label="Times a day"><input className="input num" type="number" min={1} max={12} value={r.times} onChange={(e) => setR({ ...r, times: e.target.value })} /></Field>}
                <Field label={`Dose (${item.dose_unit || "dose"})`}><input className="input num" type="number" min={0} step="any" placeholder={String(item.dose_amount ?? 1)} value={r.dose} onChange={(e) => setR({ ...r, dose: e.target.value })} /></Field>
                <Field label="Until (optional)"><input className="input" type="date" value={r.end} onChange={(e) => setR({ ...r, end: e.target.value })} /></Field>
              </div>
              <Field label="Who advised it"><input className="input" value={r.source} onChange={(e) => setR({ ...r, source: e.target.value })} placeholder="e.g. Health visitor, GP, label" /></Field>
              <Field label="Instructions as given"><input className="input" value={r.instructions} onChange={(e) => setR({ ...r, instructions: e.target.value })} /></Field>
              <div className="row-wrap"><button className="btn primary small" onClick={addRegimen}>Save</button><button className="btn ghost small" onClick={() => setR(null)}>Cancel</button></div>
            </div>
          )}
        </div>
      </div>
    </Sheet>
  );
}
