import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Icon from "../components/Icon";
import { ErrorNote, Field, LOCATIONS, Loading, Notice, Sheet, useApi, useToast } from "../components/ui";
import { api, errorText, uid } from "../lib/api";
import { fmtDate, money } from "../lib/format";
import { useWide } from "./Recipes";

// Show the retailer's exact wording only when it says more than the reviewed description.
function rawExtra(l: any): string | null {
  const lines = (l.raw_text || "").split("\n");
  const first = lines[0].replace(/\s{2,}.*$/, "").trim();
  if (l.corrected || first.toUpperCase() !== (l.description || "").toUpperCase()) return l.raw_text;
  return lines.length > 1 ? lines.slice(1).join("\n") : null;
}

export default function ReceiptReview() {
  const { id } = useParams();
  const { data: r, error, loading, reload, setData } = useApi<any>(`/receipts/${id}/review`);
  const [matching, setMatching] = useState<any>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<any>(null);
  const [justResolved, setJustResolved] = useState<string | null>(null);
  const applyKey = useRef(uid());
  const toast = useToast();
  const wide = useWide(768);

  if (error) return <ErrorNote error={error} retry={reload} />;
  if (loading && !r) return <Loading lines={8} />;
  if (!r) return null;
  const editable = r.status === "review";
  const rec = r.reconciliation;
  const items = r.lines.filter((l: any) => l.kind !== "ignore");

  async function patchHeader(body: any) { try { setData(await api(`/receipts/${id}`, { method: "PATCH", body })); } catch (e) { toast(errorText(e)); } }
  async function apply(confirm = false) {
    setApplying(true); setApplyError(null);
    try {
      const res = await api(`/receipts/${id}/apply`, { body: { idempotency_key: applyKey.current, confirm_not_duplicate: confirm } });
      setData(res.receipt);
      toast(res.status === "already_applied" ? "This receipt was already added" : `Added ${res.lots} items to the pantry and recorded ${res.purchases} prices`);
    } catch (e: any) { setApplyError(e.detail || errorText(e)); } finally { setApplying(false); }
  }
  async function deleteFile() {
    if (!confirm("Delete the original photo/file? The reviewed lines and prices stay.")) return;
    await api(`/receipts/${id}/file`, { method: "DELETE" }); reload(); toast("Original deleted");
  }

  return (
    <>
      <div className="page-head">
        <div>
          <Link to="/receipts" className="small" style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Icon name="left" size={16} />Receipts</Link>
          <div className="eyebrow" style={{ marginTop: 8 }}>{r.status === "applied" ? `Added ${fmtDate(r.applied_at)}` : "Review receipt"}</div>
          <h1>{r.retailer || "Unknown shop"} · <span className="num">{fmtDate(r.purchased_at)}</span></h1>
        </div>
        {editable && (
          <button className="btn primary big" disabled={applying || r.blocking.length > 0} onClick={() => apply(false)}>
            <Icon name="check" />{applying ? "Adding…" : r.blocking.length ? `${r.blocking.length} to resolve` : "Add to pantry"}
          </button>
        )}
      </div>

      {r.duplicate_of && r.status !== "applied" && (
        <div style={{ marginBottom: 16 }}><Notice tone="error">This looks like a receipt you've already imported ({r.duplicate_of.retailer}, {fmtDate(r.duplicate_of.purchased_at)}, {money(r.duplicate_of.total_pence)}, {r.duplicate_of.status === "applied" ? "already added" : "not yet added"}). Adding it again would count everything twice. <Link to={`/receipts/${r.duplicate_of.id}`}>Open the earlier receipt</Link></Notice></div>
      )}
      {applyError && (
        <div style={{ marginBottom: 16 }}>
          <Notice tone="error">{typeof applyError === "string" ? applyError : applyError.message}
            {applyError.code === "duplicate" && <div style={{ marginTop: 8 }}><button className="btn small danger" onClick={() => apply(true)}>It's a different shop — add anyway</button></div>}
          </Notice>
        </div>
      )}
      {r.warnings.map((w: string, i: number) => <div key={i} style={{ marginBottom: 8 }}><Notice tone="warn">{w}</Notice></div>)}

      <div className="split" style={{ gridTemplateColumns: undefined }}>
        <aside className="pane-sticky stack">
          {r.has_file && r.file_mime?.startsWith("image/") && <img src={`/api/v1/receipts/${id}/file`} alt="Original receipt" style={{ borderRadius: 10, border: "1px solid var(--rule)", maxHeight: "60dvh", objectFit: "contain", background: "#fff", width: "100%" }} />}
          {r.has_file && !r.file_mime?.startsWith("image/") && <a className="btn" href={`/api/v1/receipts/${id}/file`} target="_blank" rel="noreferrer">Open original ({r.file_mime})</a>}
          <div className="panel stack-sm small">
            <div className="grid-2">
              <Field label="Shop"><input className="input" defaultValue={r.retailer || ""} disabled={!editable} onBlur={(e) => e.target.value !== (r.retailer || "") && patchHeader({ retailer: e.target.value })} /></Field>
              <Field label="Date"><input className="input" type="date" defaultValue={r.purchased_at?.slice(0, 10) || ""} disabled={!editable} onBlur={(e) => e.target.value && patchHeader({ purchased_at: `${e.target.value}T12:00:00+00:00` })} /></Field>
            </div>
            <Field label="Printed total (£)"><input className="input num" type="number" step="0.01" defaultValue={r.total_pence !== null ? (r.total_pence / 100).toFixed(2) : ""} disabled={!editable}
              onBlur={(e) => e.target.value && patchHeader({ total_pence: Math.round(parseFloat(e.target.value) * 100) })} /></Field>
            <div className="xsmall muted">Read by: {r.parser}{r.parse_confidence !== null ? ` · confidence ${Math.round(r.parse_confidence * 100)}%` : ""}</div>
            {r.has_file ? <button className="btn ghost small" onClick={deleteFile}><Icon name="trash" size={16} />Delete original file</button>
              : <div className="xsmall muted">{r.file_deleted_at ? `Original file deleted ${fmtDate(r.file_deleted_at)}.` : "Entered as text; no file kept."}</div>}
          </div>
        </aside>

        <div>
          {!wide && <PhoneLines items={items} r={r} editable={editable} onMatch={setMatching} justResolved={justResolved} />}
          <table className="ledger" style={wide ? undefined : { display: "none" }}>
            <thead><tr><th>Line</th><th className="money">Qty</th><th className="money">Price</th><th className="money">Paid</th><th>Goes to</th></tr></thead>
            <tbody>
              {items.map((l: any) => {
                const unresolved = l.kind === "item" && l.create_lot && !["confirmed", "skip"].includes(l.match_status);
                const cls = l.kind === "discount" ? "discount" : l.match_status === "skip" ? "skipped" : unresolved ? "unresolved" : l.kind === "item" ? "resolved" : "";
                return (
                  <tr key={l.id} className={`${cls} ${justResolved === l.id ? "resolving" : ""}`}>
                    <td>
                      <div style={{ fontWeight: l.kind === "item" ? 600 : 400 }}>{l.description}{l.corrected && <span className="tag plain" style={{ marginLeft: 6 }}>edited</span>}</div>
                      {rawExtra(l) && <div className="raw">{rawExtra(l)}</div>}
                      {l.promotions.length > 0 && <div className="xsmall" style={{ color: "var(--herb-ink)" }}>{l.promotions.join("; ")}</div>}
                      {l.kind === "discount" && <div className="xsmall muted">{l.applies_to_line_id ? `Applied to ${r.lines.find((x: any) => x.id === l.applies_to_line_id)?.description}` : "Not linked to an item"}</div>}
                    </td>
                    <td className="money num">{l.weight_kg ? `${l.weight_kg} kg` : l.quantity}</td>
                    <td className="money">{money(l.line_total_pence)}</td>
                    <td className="money">{l.kind === "item" ? <strong>{money(l.paid_pence)}</strong> : ""}</td>
                    <td style={{ minWidth: 140 }}>
                      {l.kind === "item" ? (
                        <button className={`btn small ${unresolved ? "" : "ghost"}`} disabled={!editable} onClick={() => setMatching(l)} style={{ justifyContent: "flex-start", maxWidth: 220, whiteSpace: "normal", textAlign: "left" }}>
                          {l.match_status === "skip" ? "Not for the pantry" : l.product ? `${l.product.name}${l.match_status === "suggested" ? " ?" : ""}` : l.food ? `${l.food.name}${l.match_status === "suggested" ? " ?" : ""}` : "Match…"}
                        </button>
                      ) : <span className="xsmall muted">{l.kind}</span>}
                      {l.match_status === "suggested" && editable && <div className="xsmall" style={{ color: "var(--ochre-ink)" }}>Suggested; confirm</div>}
                      {l.match_status === "confirmed" && <div className="xsmall muted">{l.location}{l.is_substitution ? " · substitution" : ""}</div>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr><td colSpan={3}>Lines total</td><td className="money"><strong>{money(rec.lines_total_pence)}</strong></td><td /></tr>
              <tr><td colSpan={3}>Printed total</td><td className="money">{money(rec.printed_total_pence)}</td><td>{rec.balanced ? <span className="tag herb">Balanced</span> : rec.unmatched_pence !== null && <span className="tag tomato">{money(rec.unmatched_pence)} unmatched</span>}</td></tr>
              {r.basket_adjustments.map((b: any, i: number) => <tr key={i}><td colSpan={3} className="muted">{b.description} <span className="xsmall">(basket-level, not split across items)</span></td><td className="money muted">{money(b.amount_pence)}</td><td /></tr>)}
              {rec.payment_total_pence !== null && <tr><td colSpan={3} className="muted">Paid by card/cash</td><td className="money muted">{money(rec.payment_total_pence)}</td><td /></tr>}
            </tfoot>
          </table>
          {!wide && (
            <dl className="facts small" style={{ marginTop: 12 }}>
              <div><dt>Lines</dt><dd>{money(rec.lines_total_pence)}</dd></div>
              <div><dt>Printed total</dt><dd>{money(rec.printed_total_pence)}</dd></div>
              <div><dt>Check</dt><dd>{rec.balanced ? <span className="tag herb">Balanced</span> : rec.unmatched_pence !== null ? <span className="tag tomato">{money(rec.unmatched_pence)} unmatched</span> : "—"}</dd></div>
              {r.basket_adjustments.map((b: any, i: number) => <div key={i}><dt>{b.description}</dt><dd className="muted">{money(b.amount_pence)}</dd></div>)}
            </dl>
          )}
          {editable && r.blocking.length > 0 && <p className="small muted" style={{ marginTop: 12 }}>Highlighted lines need a match or “Not for the pantry” before the receipt can be added.</p>}
          {r.status === "applied" && <div style={{ marginTop: 16 }}><Notice tone="ok">Added. Purchases and prices are now part of your history; use the pantry to correct stock, which keeps a record of the change.</Notice></div>}
        </div>
      </div>
      <MatchSheet line={matching} receiptId={id!} onClose={() => setMatching(null)} onSaved={(rr, lineId) => { setData(rr); setMatching(null); setJustResolved(lineId); setTimeout(() => setJustResolved(null), 400); }} />
    </>
  );
}

function PhoneLines({ items, r, editable, onMatch, justResolved }: { items: any[]; r: any; editable: boolean; onMatch: (l: any) => void; justResolved: string | null }) {
  return (
    <div style={{ borderTop: "1px solid var(--rule-strong)" }}>
      {items.map((l: any) => {
        const unresolved = l.kind === "item" && l.create_lot && !["confirmed", "skip"].includes(l.match_status);
        const cls = l.kind === "discount" ? "discount" : l.match_status === "skip" ? "skipped" : unresolved ? "unresolved" : l.kind === "item" ? "resolved" : "";
        const raw = rawExtra(l);
        return (
          <div key={l.id} className={`rline ${cls} ${justResolved === l.id ? "resolving" : ""}`}>
            <div className="spread" style={{ alignItems: "flex-start" }}>
              <div className="grow">
                <div style={{ fontWeight: l.kind === "item" ? 600 : 400 }}>{l.description}</div>
                {raw && <div className="raw">{raw}</div>}
                {l.promotions.length > 0 && <div className="xsmall" style={{ color: "var(--herb-ink)" }}>{l.promotions.join("; ")}</div>}
              </div>
              <div className="amounts">
                <div style={{ fontWeight: 600 }}>{money(l.kind === "item" ? l.paid_pence : l.line_total_pence)}</div>
                {l.kind === "item" && l.discount_pence > 0 && <div className="xsmall muted" style={{ textDecoration: "line-through" }}>{money(l.line_total_pence)}</div>}
                <div className="xsmall muted">{l.weight_kg ? `${l.weight_kg} kg` : l.quantity > 1 ? `${l.quantity} ×` : ""}</div>
              </div>
            </div>
            {l.kind === "item" && (
              <div className="row" style={{ marginTop: 8 }}>
                <button className={`btn small ${unresolved ? "" : "ghost"}`} disabled={!editable} onClick={() => onMatch(l)} style={{ justifyContent: "flex-start" }}>
                  {l.match_status === "skip" ? "Not for the pantry" : l.product ? `${l.product.name}${l.match_status === "suggested" ? " ?" : ""}` : l.food ? `${l.food.name}${l.match_status === "suggested" ? " ?" : ""}` : "Match…"}
                </button>
                {l.match_status === "suggested" && editable && <span className="xsmall" style={{ color: "var(--ochre-ink)" }}>Suggested; confirm</span>}
                {l.match_status === "confirmed" && <span className="xsmall muted">→ {l.location}</span>}
              </div>
            )}
            {l.kind === "discount" && <div className="xsmall muted">{l.applies_to_line_id ? `Applied to ${r.lines.find((x: any) => x.id === l.applies_to_line_id)?.description}` : "Not linked to an item"}</div>}
          </div>
        );
      })}
    </div>
  );
}

function MatchSheet({ line, receiptId, onClose, onSaved }: { line: any; receiptId: string; onClose: () => void; onSaved: (r: any, lineId: string) => void }) {
  const [q, setQ] = useState("");
  const products = useApi<any[]>(line ? `/products?q=${encodeURIComponent(q || line.description.split(" ").slice(0, 2).join(" "))}&limit=8` : null, [line?.id, q]);
  const [desc, setDesc] = useState<string | null>(null);
  const [qty, setQty] = useState<string>("");
  const [price, setPrice] = useState<string>("");
  const [loc, setLoc] = useState<string | null>(null);
  const [useBy, setUseBy] = useState("");
  const [kg, setKg] = useState("");
  const [newP, setNewP] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  if (!line) return null;
  async function send(extra: any) {
    setError(null);
    try {
      const body: any = { ...extra, location: loc || line.location };
      if (desc !== null && desc !== line.description) body.description = desc;
      if (qty) body.quantity = parseFloat(qty);
      if (price) body.line_total_pence = Math.round(parseFloat(price) * 100);
      if (useBy) body.use_by = useBy;
      if (kg) body.weight_kg = parseFloat(kg);
      const r = await api(`/receipts/${receiptId}/lines/${line.id}`, { method: "PATCH", body });
      setDesc(null); setQty(""); setPrice(""); setLoc(null); setNewP(null); setUseBy(""); setKg("");
      onSaved(r, line.id);
    } catch (e) { setError(errorText(e)); }
  }
  return (
    <Sheet open={!!line} onClose={onClose} title="Match this line">
      <div className="stack">
        <div className="raw panel">{line.raw_text}</div>
        <Field label="Description" hint="Correct the reading if needed; the original wording above is kept."><input className="input" value={desc ?? line.description} onChange={(e) => setDesc(e.target.value)} /></Field>
        <div className="grid-3">
          <Field label="Qty"><input className="input num" type="number" step="any" min={0} placeholder={String(line.quantity)} value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
          <Field label="Line price (£)"><input className="input num" type="number" step="0.01" placeholder={line.line_total_pence !== null ? (line.line_total_pence / 100).toFixed(2) : ""} value={price} onChange={(e) => setPrice(e.target.value)} /></Field>
          <Field label="Goes in"><select className="input" value={loc || line.location} onChange={(e) => setLoc(e.target.value)}>{LOCATIONS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}</select></Field>
        </div>
        <div className="grid-2">
          <Field label="Use by (optional)"><input className="input" type="date" value={useBy} onChange={(e) => setUseBy(e.target.value)} /></Field>
          <Field label="Weight, loose items (kg)"><input className="input num" type="number" step="0.001" min={0} placeholder={line.weight_kg ?? ""} value={kg} onChange={(e) => setKg(e.target.value)} /></Field>
        </div>
        {line.match_status === "suggested" && (line.product || line.food) && <button className="btn primary" onClick={() => send(line.product ? { product_id: line.product.id } : { food_id: line.food.id })}><Icon name="check" />Yes, it's {line.product?.name || line.food?.name}</button>}
        <Field label="Find a product"><input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your products" /></Field>
        <ul className="list">
          {(products.data || []).map((p) => (
            <li key={p.id}><button className="list-row" style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }} onClick={() => send({ product_id: p.id })}>
              <span className="grow"><span className="title">{p.name}</span><span className="meta" style={{ display: "block" }}>{p.brand}{p.package_quantity ? ` · ${p.package_quantity} ${p.package_unit}` : " · no pack size"}</span></span><Icon name="right" />
            </button></li>
          ))}
        </ul>
        {!newP ? <button className="btn" onClick={() => setNewP({ name: desc ?? line.description, package_quantity: "", package_unit: "g", food_name: "" })}><Icon name="plus" />New product</button> : (
          <div className="panel stack-sm">
            <Field label="Product name"><input className="input" value={newP.name} onChange={(e) => setNewP({ ...newP, name: e.target.value })} /></Field>
            <div className="grid-2">
              <Field label="Pack size"><input className="input num" type="number" step="any" value={newP.package_quantity} onChange={(e) => setNewP({ ...newP, package_quantity: e.target.value })} /></Field>
              <Field label="Unit"><select className="input" value={newP.package_unit} onChange={(e) => setNewP({ ...newP, package_unit: e.target.value })}>{["g", "kg", "ml", "l", "each"].map((u) => <option key={u}>{u}</option>)}</select></Field>
            </div>
            <Field label="Generic food" hint="Links it to recipes, e.g. “Dried pasta”"><input className="input" value={newP.food_name} onChange={(e) => setNewP({ ...newP, food_name: e.target.value })} /></Field>
            <button className="btn primary" onClick={() => send({ new_product: { name: newP.name, package_quantity: newP.package_quantity ? parseFloat(newP.package_quantity) : null, package_unit: newP.package_unit, food_name: newP.food_name || newP.name } })}>Create and match</button>
          </div>
        )}
        <div className="row-wrap">
          <button className="btn ghost" onClick={() => send({ food_name: desc ?? line.description })}>Loose item (by weight)</button>
          <button className="btn ghost" onClick={() => send({ is_substitution: true })}>Mark as substitution</button>
          <button className="btn ghost" onClick={() => send({ match_status: "skip" })}>Not for the pantry</button>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
      </div>
    </Sheet>
  );
}
