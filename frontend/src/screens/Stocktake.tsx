import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import { DateBadge, Empty, ErrorNote, LOCATIONS, Loading, Notice, Segmented, useApi, useToast } from "../components/ui";
import { api, errorText, uid } from "../lib/api";
import { fmtDate } from "../lib/format";

export default function Stocktake() {
  const [params, setParams] = useSearchParams();
  const loc = params.get("location") || "fridge";
  const { data, error, loading, reload } = useApi<any>(`/pantry/stocktake?location=${loc}`, [loc]);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [gone, setGone] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const toast = useToast();
  const nav = useNavigate();
  useEffect(() => { setCounts({}); setGone({}); setResult(null); }, [loc]);
  const lots = (data?.lots || []) as any[];
  const touched = Object.keys(counts).length + Object.values(gone).filter(Boolean).length;

  async function save() {
    setBusy(true);
    try {
      const body = {
        location: loc,
        counts: lots.filter((l) => !gone[l.id] && counts[l.id] !== undefined && counts[l.id] !== "")
          .map((l) => ({ lot_id: l.id, counted_quantity: parseFloat(counts[l.id]), unit: l.base_unit })),
        not_found: lots.filter((l) => gone[l.id]).map((l) => l.id), idempotency_key: uid(),
      };
      const r = await api("/pantry/stocktake", { body });
      setResult(r);
      toast(`${LOCATIONS.find((x) => x.value === loc)?.label} counted: ${r.changed.length} corrected, ${r.not_found.length} not found`);
      reload();
    } catch (e) { toast(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <>
      <div className="page-head">
        <div><Link to="/pantry" className="small" style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Icon name="left" size={16} />Pantry</Link>
          <div className="eyebrow" style={{ marginTop: 8 }}>Stocktake</div><h1>Count what's there</h1></div>
        <div className="row-wrap">
          <button className="btn" onClick={() => nav("/pantry")}>Done</button>
          <button className="btn primary" disabled={busy || !touched} onClick={save}><Icon name="check" />Save count</button>
        </div>
      </div>
      <Segmented label="Location" value={loc} onChange={(v) => setParams({ location: v }, { replace: true })} options={LOCATIONS.map((l) => ({ value: l.value, label: l.label }))} />
      <p className="small muted" style={{ margin: "12px 0" }}>
        {data?.last_counted?.[loc] ? `Last counted ${fmtDate(data.last_counted[loc])}. ` : "Not counted yet. "}
        Leave a line blank if it's right; enter what you find, or tick “Not here”. Differences are saved as corrections, so the history stays intact.
      </p>
      {result && <Notice tone="ok">{result.changed.length} corrected ({result.changed.map((c: any) => `${c.before} → ${c.after}`).join(", ") || "none"}), {result.unchanged} confirmed, {result.not_found.length} not found.</Notice>}
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={6} />}
      {data && lots.length === 0 && <Empty icon="jar" title="Nothing recorded here">Anything you find can be added with Scan or add.</Empty>}
      <ul className="list" style={{ marginTop: 8 }}>
        {lots.map((l) => (
          <li key={l.id} className="list-row" style={{ opacity: gone[l.id] ? 0.55 : 1 }}>
            <span className="grow">
              <span className="title" style={{ display: "block" }}>{l.name}{l.brand ? <span className="muted"> · {l.brand}</span> : null}</span>
              <span className="meta">Recorded <span className="num">{l.display}</span>{l.opened ? " · opened" : ""} </span>
              <DateBadge kind={l.relevant_date_kind} date={l.relevant_date} label={l.date_label} />
            </span>
            <label className="row xsmall"><input type="checkbox" checked={!!gone[l.id]} onChange={(e) => setGone({ ...gone, [l.id]: e.target.checked })} /> Not here</label>
            <input className="input num" style={{ width: 92, minHeight: 40 }} type="number" min={0} step="any" inputMode="decimal" disabled={!!gone[l.id]}
              aria-label={`Counted ${l.name} in ${l.base_unit}`} placeholder={String(Math.round(l.quantity_base * 100) / 100)}
              value={counts[l.id] ?? ""} onChange={(e) => setCounts({ ...counts, [l.id]: e.target.value })} />
            <span className="small" style={{ width: 36 }}>{l.base_unit}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
