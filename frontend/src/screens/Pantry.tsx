import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import { DateBadge, Empty, ErrorNote, Field, LOCATIONS, Loading, Notice, Segmented, Sheet, useApi, useToast } from "../components/ui";
import { api, errorText, uid } from "../lib/api";
import { fmtDate, fmtDay, money, perUnit } from "../lib/format";
import { useWide } from "./Recipes";

type Group = any;

function quickStep(g: Group): { qty: number; unit: string; label: string } | null {
  const lot = g.lots[0];
  if (!lot) return null;
  const pack = g.product?.package_base_quantity;
  if (g.base_unit === "each") return { qty: Math.min(1, lot.quantity_base), unit: "each", label: "Use 1" };
  if (pack && lot.quantity_base >= pack - 1e-6) return { qty: pack, unit: g.base_unit, label: "Use 1 pack" };
  return null;
}

export default function Pantry() {
  const [params, setParams] = useSearchParams();
  const [loc, setLoc] = useState<string>("all");
  const [q, setQ] = useState("");
  const [pet, setPet] = useState(false);
  const { data, error, loading, reload } = useApi<any>(`/pantry?${loc !== "all" ? `location=${loc}&` : ""}${pet ? "pet=true" : ""}`, [loc, pet]);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [bumped, setBumped] = useState<string | null>(null);
  const wide = useWide(1024);
  const toast = useToast();

  const items: Group[] = (data?.items || []).filter((g: Group) => !q || g.name.toLowerCase().includes(q.toLowerCase()) || (g.brand || "").toLowerCase().includes(q.toLowerCase()));
  useEffect(() => {
    const lot = params.get("lot");
    if (lot && data) { const g = data.items.find((x: Group) => x.lots.some((l: any) => l.id === lot)); if (g) setOpenKey(g.key); params.delete("lot"); setParams(params, { replace: true }); }
  }, [data]); // eslint-disable-line
  const open = items.find((g) => g.key === openKey) || (wide ? items[0] : null);

  async function consume(g: Group, step: { qty: number; unit: string }) {
    const lot = g.lots[0];
    try {
      const r = await api("/pantry/events", { body: { lot_id: lot.id, type: "consumption", quantity: step.qty, unit: step.unit, idempotency_key: uid() } });
      setBumped(g.key);
      setTimeout(() => setBumped(null), 300);
      reload();
      toast({ text: `Used ${r.event.entered_quantity === step.qty && step.unit === "each" ? step.qty : ""} ${g.name}`.replace(/\s+/g, " "), action: { label: "Undo", run: async () => {
        await api("/pantry/events", { body: { lot_id: lot.id, type: "add", quantity: -r.event.delta_base, unit: lot.base_unit, reason: "Undo" } }); reload(); } } });
    } catch (e) { toast(errorText(e)); }
  }

  const list = (
    <>
      {items.length === 0 && !loading && (
        <Empty icon={pet ? "paw" : "jar"} title={q ? "Nothing matches" : pet ? "No pet food recorded" : "The pantry is empty"}>
          {!q && <>Scan an item or <Link to="/receipts?upload=1">review a receipt</Link> to fill it.</>}
        </Empty>
      )}
      <ul className="list">
        {items.map((g) => {
          const step = quickStep(g);
          const first = g.lots[0];
          return (
            <li key={g.key}>
              <div className="list-row" style={{ background: wide && open?.key === g.key ? "var(--card)" : undefined, boxShadow: wide && open?.key === g.key ? "inset 3px 0 0 var(--herb)" : undefined, paddingLeft: wide ? 8 : 0 }}>
                <button onClick={() => setOpenKey(g.key)} style={{ all: "unset", cursor: "pointer", display: "flex", gap: 12, alignItems: "center", flex: 1, minWidth: 0 }} aria-label={`${g.name}, ${g.display}`}>
                  {g.product?.image_url ? <img className="thumb" src={g.product.image_url} alt="" /> : <span className="thumb placeholder"><Icon name={LOCATIONS.find((l) => l.value === first?.location)?.icon || "jar"} size={20} /></span>}
                  <span className="grow">
                    <span className="title" style={{ display: "block" }}>{g.name}</span>
                    <span className="meta">{g.brand ? `${g.brand} · ` : ""}{g.locations.join(", ")}{g.lots.length > 1 ? ` · ${g.lots.length} lots` : ""}{first?.opened ? " · opened" : ""}</span>
                    <span style={{ display: "block" }}>
                      <DateBadge kind={g.earliest_date_kind} date={g.earliest_date} label={first?.date_label} />
                      {g.low_stock && <span className="tag ochre" style={{ marginLeft: 8 }}>Low</span>}
                    </span>
                  </span>
                  <span className={`qty ${bumped === g.key ? "bump" : ""}`}>{g.display}</span>
                </button>
                {step ? <button className="btn small icon-btn" onClick={() => consume(g, step)} aria-label={`${step.label} of ${g.name}`} title={step.label}><Icon name="minus" size={18} /></button>
                  : <button className="btn small icon-btn" onClick={() => setOpenKey(g.key)} aria-label={`Use some ${g.name}`}><Icon name="minus" size={18} /></button>}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">{pet ? "Pet food — kept separate from human meals" : "Pantry"}</div><h1>{pet ? "Dog's food" : "What we have"}</h1></div>
        <div className="row-wrap">
          <Link className="btn primary" to="/pantry/scan"><Icon name="scan" />Scan or add</Link>
          {!pet && <Link className="btn" to={`/pantry/stocktake?location=${loc === "all" ? "fridge" : loc}`}><Icon name="check" />Stocktake</Link>}
          <button className="btn" onClick={() => { setPet(!pet); setOpenKey(null); }}><Icon name={pet ? "jar" : "paw"} />{pet ? "Household pantry" : "Pet food"}</button>
        </div>
      </div>
      <div className="row-wrap" style={{ marginBottom: 16 }}>
        <Segmented label="Location" value={loc} onChange={(v) => { setLoc(v); setOpenKey(null); }} options={[{ value: "all", label: "All" }, ...LOCATIONS.map((l) => ({ value: l.value, label: l.label }))]} />
        <input className="input" type="search" placeholder="Find an item" aria-label="Find an item" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} />
      </div>
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={6} />}
      {wide ? (
        <div className="split" style={{ gridTemplateColumns: "minmax(380px, 1fr) minmax(360px, 440px)" }}>
          <div>{list}</div>
          <div className="pane-sticky panel" style={{ maxHeight: "calc(100dvh - 96px)", overflow: "auto" }}>{open ? <ItemDetail g={open} onChange={reload} /> : <p className="muted">Choose an item.</p>}</div>
        </div>
      ) : (
        <>
          {list}
          <Sheet open={!!open} onClose={() => setOpenKey(null)} title={open?.name || ""}>{open && <ItemDetail g={open} onChange={() => { reload(); }} />}</Sheet>
        </>
      )}
      {data && <p className="xsmall muted" style={{ marginTop: 16 }}>“Use by” and “Best before” are shown exactly as the label says. Larder never decides whether food is safe to eat.</p>}
    </>
  );
}

function ItemDetail({ g, onChange }: { g: Group; onChange: () => void }) {
  const [lotId, setLotId] = useState<string>(g.lots[0]?.id);
  const lot = g.lots.find((l: any) => l.id === lotId) || g.lots[0];
  const detail = useApi<any>(lot ? `/pantry/lots/${lot.id}` : null, [lot?.id, lot?.quantity_base, lot?.location]);
  const prices = useApi<any>(g.product ? `/prices/history?productId=${g.product.id}` : g.food ? `/prices/history?foodId=${g.food.id}` : null, [g.key]);
  const [action, setAction] = useState<string | null>(null);
  const [qty, setQty] = useState("");
  const [unit, setUnit] = useState(g.base_unit);
  const [reason, setReason] = useState("");
  const [to, setTo] = useState("fridge");
  const [err, setErr] = useState<string | null>(null);
  const [minimum, setMinimum] = useState<string>(g.threshold_base ?? "");
  const toast = useToast();
  useEffect(() => { setLotId(g.lots[0]?.id); setAction(null); }, [g.key]); // eslint-disable-line

  async function send(body: any) {
    setErr(null);
    try {
      await api("/pantry/events", { body: { lot_id: lot.id, idempotency_key: uid(), ...body } });
      setAction(null); setQty(""); setReason(""); onChange(); detail.reload(); toast("Updated");
    } catch (e) {
      const m = errorText(e);
      setErr(m);
    }
  }
  async function saveMin() {
    const val = minimum === "" ? null : parseFloat(minimum);
    if (g.product) await api(`/products/${g.product.id}`, { method: "PATCH", body: { min_stock_base: val } });
    else await api(`/foods/${g.food.id}`, { method: "PATCH", body: { min_stock_base: val } });
    toast(val === null ? "Minimum removed" : "Minimum saved"); onChange();
  }
  if (!lot) return <p className="muted">No stock left.</p>;
  const ps = prices.data?.summary;
  const units = g.base_unit === "g" ? ["g", "kg", "pack"] : g.base_unit === "ml" ? ["ml", "l", "pack"] : ["each"];

  return (
    <div className="stack">
      <div className="spread">
        <div><div className="serif" style={{ fontSize: "1.375rem" }}>{g.name}</div><div className="small muted">{g.brand}</div></div>
        <div className="qty" style={{ fontSize: "1.375rem" }}>{g.display}</div>
      </div>
      {g.lots.length > 1 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Lots</div>
          <table className="ledger small">
            <thead><tr><th>Bought</th><th>Where</th><th>Date</th><th className="money">Left</th></tr></thead>
            <tbody>{g.lots.map((l: any) => (
              <tr key={l.id} onClick={() => setLotId(l.id)} style={{ cursor: "pointer", background: l.id === lot.id ? "var(--herb-tint)" : undefined }} aria-selected={l.id === lot.id}>
                <td>{fmtDay(l.purchased_on)}{l.price_paid_pence !== null && <div className="xsmall muted">{money(l.price_paid_pence)}{l.store ? `, ${l.store}` : ""}</div>}</td>
                <td>{l.location}</td><td><DateBadge kind={l.relevant_date_kind} date={l.relevant_date} label={l.date_label} /></td><td className="money">{l.display}</td></tr>))}
            </tbody>
          </table>
        </div>
      )}
      <dl className="facts small" style={{ gap: 20 }}>
        <div><dt>Location</dt><dd>{lot.location}</dd></div>
        {lot.use_by && <div><dt>{lot.date_label || "Use by"}</dt><dd>{fmtDate(lot.use_by)}</dd></div>}
        {lot.best_before && <div><dt>Best before</dt><dd>{fmtDate(lot.best_before)}</dd></div>}
        {lot.purchased_on && <div><dt>Bought</dt><dd>{fmtDate(lot.purchased_on)}</dd></div>}
        {lot.opened && <div><dt>Status</dt><dd>Opened</dd></div>}
      </dl>
      <div className="row-wrap">
        {[["consumption", "Used some"], ["waste", "Thrown away"], ["transfer", "Move"], ["correction", "Correct count"]].map(([k, label]) => (
          <button key={k} className={`btn small ${action === k ? "primary" : ""}`} onClick={() => { setAction(action === k ? null : k); setErr(null); }}>{label}</button>
        ))}
        {!lot.opened && g.base_unit !== "each" && <button className="btn small ghost" onClick={() => send({ type: "open" })}>Mark opened</button>}
      </div>
      {action && (
        <div className="panel stack-sm" style={{ background: "var(--paper-2)" }}>
          {action === "transfer" ? (
            <Field label="Move to"><select className="input" value={to} onChange={(e) => setTo(e.target.value)}>{LOCATIONS.filter((l) => l.value !== lot.location).map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}</select></Field>
          ) : (
            <div className="row">
              <Field label={action === "correction" ? "Counted amount now" : "Amount"}><input className="input num" type="number" min={0} step="any" value={qty} onChange={(e) => setQty(e.target.value)} autoFocus /></Field>
              <Field label="Unit"><select className="input" value={unit} onChange={(e) => setUnit(e.target.value)}>{units.map((u) => <option key={u}>{u}</option>)}</select></Field>
            </div>
          )}
          {(action === "waste" || action === "correction") && <Field label={action === "correction" ? "Why (required)" : "Reason (optional)"}><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={action === "correction" ? "e.g. stocktake" : "e.g. went mouldy"} /></Field>}
          {err && <Notice tone="error">{err}</Notice>}
          {err && err.includes("below zero") && (
            <div className="stack-sm">
              <Field label="Record anyway — explain why"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. we used more than was recorded" /></Field>
              <button className="btn small danger" disabled={!reason} onClick={() => send({ type: action, quantity: qty ? parseFloat(qty) : null, unit, reason, negative_override: true, override_reason: reason })}>Record with override</button>
            </div>
          )}
          <div className="row-wrap">
            <button className="btn primary small" onClick={() => {
              if (action === "transfer") return send({ type: "transfer", to_location: to });
              if (action === "correction") return send({ type: "correction", new_quantity: parseFloat(qty), unit, reason });
              return send({ type: action, quantity: qty ? parseFloat(qty) : null, unit, reason: reason || null });
            }}>Save</button>
            {(action === "consumption" || action === "waste") && <button className="btn small" onClick={() => send({ type: action, quantity: null, reason: reason || "Used it all" })}>All of it</button>}
          </div>
        </div>
      )}

      {ps && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Prices you've paid</div>
          <dl className="facts small" style={{ gap: 20, borderBottom: 0 }}>
            <div><dt>Usually</dt><dd>{perUnit(ps.usually_paid, ps.standard_unit)}</dd></div>
            <div><dt>Lowest</dt><dd>{perUnit(ps.lowest, ps.standard_unit)}</dd></div>
            <div><dt>Highest</dt><dd>{perUnit(ps.highest, ps.standard_unit)}</dd></div>
            <div><dt>Last</dt><dd>{money(ps.last_purchased.price_pence)} <span className="muted xsmall">{ps.last_purchased.store}</span></dd></div>
          </dl>
          <p className="xsmall muted">{ps.basis}; {ps.observations} purchases. Different pack sizes and shops are kept separate.</p>
        </div>
      )}

      <div className="row">
        <Field label={`Remind me below (${g.base_unit})`}><input className="input num" type="number" min={0} value={minimum} onChange={(e) => setMinimum(e.target.value)} /></Field>
        <button className="btn small" style={{ alignSelf: "flex-end", marginBottom: 2 }} onClick={saveMin}>Save</button>
      </div>

      {detail.data && (
        <details>
          <summary className="btn ghost small"><Icon name="history" size={18} />History ({detail.data.events.length})</summary>
          <table className="ledger xsmall" style={{ marginTop: 8 }}>
            <tbody>{[...detail.data.events].reverse().map((e: any) => (
              <tr key={e.id}><td>{fmtDay(e.occurred_at)}</td><td>{e.type}{e.reason ? <div className="muted">{e.reason}</div> : null}{e.negative_override ? <div style={{ color: "var(--tomato-ink)" }}>override: {e.override_reason}</div> : null}</td>
                <td className="money">{e.delta_base > 0 ? "+" : ""}{Math.round(e.delta_base * 100) / 100} {lot.base_unit}</td><td className="money muted">{Math.round(e.resulting_quantity_base * 100) / 100}</td></tr>))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
