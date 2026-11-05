import { useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import { Empty, ErrorNote, Field, Loading, Notice, Segmented, Sheet, useApi } from "../components/ui";
import { api, errorText, waitForJob } from "../lib/api";
import { fmtDate, money } from "../lib/format";

export default function Receipts() {
  const [params, setParams] = useSearchParams();
  const { data, error, loading, reload } = useApi<any[]>("/receipts");
  const open = params.get("upload") === "1";
  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Purchases</div><h1>Receipts</h1></div>
        <button className="btn primary" onClick={() => setParams({ upload: "1" })}><Icon name="upload" />Add a receipt</button>
      </div>
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={5} />}
      {data?.length === 0 && <Empty icon="receipt" title="No receipts yet">Photograph a till receipt, or upload a Sainsbury's or Tesco e-receipt PDF, a Lidl Plus or Sparks screenshot.</Empty>}
      {data && data.length > 0 && (
        <table className="ledger">
          <thead><tr><th>Date</th><th>Shop</th><th>Status</th><th className="money">Total</th></tr></thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.id} className={r.status === "review" ? "unresolved" : r.status === "applied" ? "resolved" : ""}>
                <td><Link to={`/receipts/${r.id}`}>{fmtDate(r.purchased_at || r.created_at)}</Link></td>
                <td>{r.retailer || "Unknown shop"}<div className="xsmall muted">{r.line_count} lines · {r.source_kind}</div></td>
                <td>{r.status === "review" ? <span className="tag ochre">To review</span> : r.status === "applied" ? <span className="tag herb">Added</span> : r.status === "parsing" ? <span className="tag plain">Reading…</span> : <span className="tag tomato">{r.status}</span>}
                  {r.duplicate_of_id && r.status !== "applied" && <span className="tag tomato" style={{ marginLeft: 6 }}>Possible duplicate</span>}</td>
                <td className="money">{money(r.total_pence)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Upload open={open} onClose={() => { setParams({}); reload(); }} />
    </>
  );
}

function Upload({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<"file" | "text">("file");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dup, setDup] = useState<any>(null);
  const file = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  async function go() {
    setError(null); setDup(null);
    const fd = new FormData();
    if (mode === "file") { const f = file.current?.files?.[0]; if (!f) { setError("Choose a photo or file"); return; } fd.append("file", f); }
    else fd.append("text", text);
    setBusy("Uploading…");
    try {
      const r = await api("/receipts/import", { form: fd });
      if (r.duplicate) { setDup(r); setBusy(null); return; }
      setBusy("Reading the receipt…");
      if (r.job && r.job.status !== "succeeded" && r.job.status !== "failed") await waitForJob(r.job.id);
      nav(`/receipts/${r.receipt.id}`);
    } catch (e) { setError(errorText(e)); setBusy(null); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Review a receipt">
      <div className="stack">
        <Segmented label="Receipt source" value={mode} onChange={setMode} options={[{ value: "file", label: "Photo or file" }, { value: "text", label: "Paste text" }]} />
        {mode === "file" ? (
          <Field label="Receipt" hint="Phone photo, screenshot, PDF e-receipt or a saved e-mail (.eml). Up to 15 MB.">
            <input ref={file} className="input" type="file" accept="image/*,application/pdf,.eml,.html,.txt" capture="environment" />
          </Field>
        ) : (
          <Field label="Receipt text"><textarea className="input" style={{ minHeight: 200 }} value={text} onChange={(e) => setText(e.target.value)} /></Field>
        )}
        <p className="xsmall muted">Nothing is added to the pantry until you've checked each line and pressed “Add to pantry”. The original is kept with the receipt and can be deleted separately.</p>
        {dup && <Notice tone="warn">{dup.message} <Link to={`/receipts/${dup.receipt.id}`}>Open the earlier one</Link></Notice>}
        {error && <Notice tone="error">{error}</Notice>}
        <button className="btn primary big" disabled={!!busy} onClick={go}>{busy || "Read receipt"}</button>
      </div>
    </Sheet>
  );
}
