import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import { Field, LOCATIONS, Notice, Segmented, useToast } from "../components/ui";
import { api, errorText, uid } from "../lib/api";

type Found = { status: "known" | "draft" | "unknown"; barcode: string; product?: any; draft?: any; existing_lots?: any[]; message?: string; warning?: string; review_note?: string; suggested?: any };

function Camera({ onCode, paused }: { onCode: (code: string) => void; paused: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<"starting" | "on" | "denied" | "unsupported">("starting");
  const last = useRef<{ code: string; at: number }>({ code: "", at: 0 });
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const emit = useCallback((code: string) => {
    if (pausedRef.current) return;
    const now = Date.now();
    if (code === last.current.code && now - last.current.at < 3000) return;
    last.current = { code, at: now };
    navigator.vibrate?.(40);
    onCode(code);
  }, [onCode]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let stop = false;
    let controls: any = null;
    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) { setState("unsupported"); return; }
      try {
        const BD = (window as any).BarcodeDetector;
        if (BD) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
          if (!video.current) return;
          video.current.srcObject = stream;
          await video.current.play();
          setState("on");
          const det = new BD({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"] });
          const tick = async () => {
            if (stop || !video.current) return;
            try { const codes = await det.detect(video.current); if (codes[0]?.rawValue) emit(codes[0].rawValue); } catch { /* frame not ready */ }
            setTimeout(tick, 250);
          };
          tick();
        } else {
          // iOS Safari: no BarcodeDetector, use ZXing (loaded only when needed)
          const { BrowserMultiFormatReader } = await import("@zxing/browser");
          const reader = new BrowserMultiFormatReader();
          controls = await reader.decodeFromConstraints({ video: { facingMode: "environment" } }, video.current!, (res) => { if (res) emit(res.getText()); });
          setState("on");
        }
      } catch (e: any) {
        setState(e?.name === "NotAllowedError" ? "denied" : "unsupported");
      }
    })();
    return () => { stop = true; stream?.getTracks().forEach((t) => t.stop()); controls?.stop?.(); };
  }, [emit]);

  return (
    <div>
      <div className="scanner">
        <video ref={video} playsInline muted aria-label="Camera preview" />
        {state === "on" && <div className="reticle" aria-hidden />}
        {state !== "on" && (
          <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "#fff", textAlign: "center", padding: 24 }}>
            <div>
              <Icon name="camera" size={32} />
              <p style={{ marginTop: 8 }}>{state === "starting" ? "Starting the camera…" : state === "denied" ? "Camera access was declined. You can allow it in your browser settings, or type the barcode below." : "The camera isn't available here. Type the barcode or add the item without one."}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Scan() {
  const toast = useToast();
  const [mode, setMode] = useState<"camera" | "type" | "nobarcode">("camera");
  const [batch, setBatch] = useState(false);
  const [found, setFound] = useState<Found | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [tally, setTally] = useState<{ name: string; qty: string }[]>([]);
  const [form, setForm] = useState<any>({});
  const setF = (p: any) => setForm((f: any) => ({ ...f, ...p }));

  const lookup = useCallback(async (code: string) => {
    setBusy(true); setError(null);
    try {
      const r: Found = await api("/pantry/scan", { body: { barcode: code } });
      setFound(r);
      const d = r.draft || {};
      setForm({
        name: r.product?.name || d.name || "", brand: r.product?.brand || d.brand || "", size: r.product?.package_quantity ?? d.package_quantity ?? "",
        size_unit: r.product?.package_unit || d.package_unit || "g", quantity: 1, unit: "pack", location: r.suggested?.location || "cupboard",
        best_before: "", use_by: "", store: "", price: "", food_name: d.categories?.[0] ? "" : "",
      });
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const common = { quantity: parseFloat(form.quantity) || 1, unit: form.unit, location: form.location, best_before: form.best_before || null, use_by: form.use_by || null,
        store: form.store || null, price_paid_pence: form.price ? Math.round(parseFloat(form.price) * 100) : null, event_type: form.price || form.store ? "purchase" : "add", confirm_duplicate: !!form.confirm_duplicate };
      let body: any;
      if (mode === "nobarcode") {
        body = { ...common, food_name: form.name, unit: form.unit === "pack" ? "each" : form.unit };
      } else if (found?.status === "known") {
        body = { ...common, product_id: found.product.id };
      } else {
        const d = found?.draft;
        body = { ...common, new_product: { name: form.name, brand: form.brand || null, package_quantity: form.size ? parseFloat(form.size) : null, package_unit: form.size_unit,
          barcode: found?.barcode, food_name: form.food_name || form.name, image_url: d?.image_url || null, image_source: d?.attribution || null,
          ingredients_text: d?.ingredients_text || null, allergens: d?.allergens || [], traces: d?.traces || [], provenance: d ? d.provenance : "manual",
          nutrition: d?.nutrition ? { ...d.nutrition, source: "open_food_facts", verified: false } : null } };
      }
      await api("/pantry/lots", { body, idempotencyKey: uid() });
      const label = `${form.name}${form.brand ? ` (${form.brand})` : ""}`;
      setTally((t) => [{ name: label, qty: `${common.quantity} ${common.unit}` }, ...t]);
      toast(`Added ${label}`);
      setFound(null); setForm({}); setTyped("");
    } catch (err: any) {
      if (err?.detail?.code === "possible_duplicate") { setError(err.detail.message); setF({ confirm_duplicate: true }); }
      else setError(errorText(err));
    } finally { setBusy(false); }
  }

  const showForm = found || mode === "nobarcode";
  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Pantry</div><h1>Scan or add</h1></div>
        <label className="row small"><input type="checkbox" checked={batch} onChange={(e) => setBatch(e.target.checked)} /> Batch mode — keep the camera on</label>
      </div>
      <div className="two-col">
        <div className="stack">
          <Segmented label="Add by" value={mode} onChange={(m) => { setMode(m); setFound(null); setError(null); if (m === "nobarcode") setForm({ name: "", quantity: 1, unit: "g", location: "fridge" }); }}
            options={[{ value: "camera", label: "Scan barcode" }, { value: "type", label: "Type barcode" }, { value: "nobarcode", label: "No barcode" }]} />
          {mode === "camera" && (!found || batch) && <Camera onCode={lookup} paused={busy || (!!found && !batch)} />}
          {mode === "type" && !found && (
            <form className="row" onSubmit={(e) => { e.preventDefault(); lookup(typed.trim()); }}>
              <input className="input num" inputMode="numeric" pattern="[0-9]{6,14}" placeholder="e.g. 5000159484695" aria-label="Barcode" value={typed} onChange={(e) => setTyped(e.target.value)} required />
              <button className="btn primary">Look up</button>
            </form>
          )}
          {busy && !showForm && <p className="muted">Looking that up…</p>}
          {error && <Notice tone={error.includes("just added") ? "warn" : "error"}>{error}</Notice>}
          {tally.length > 0 && (
            <div>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Added this session ({tally.length})</div>
              <ul className="list small">{tally.map((t, i) => <li key={i} className="list-row" style={{ minHeight: 44, padding: "6px 0" }}><span className="grow">{t.name}</span><span className="num muted">{t.qty}</span></li>)}</ul>
              <Link to="/pantry" className="btn small" style={{ marginTop: 8 }}>Done — view pantry</Link>
            </div>
          )}
        </div>

        <div>
          {showForm && (
            <form className="panel stack" onSubmit={save}>
              {found?.status === "known" && <Notice tone="ok">Known item. Check the details and add it.</Notice>}
              {found?.status === "draft" && <Notice tone="warn">{found.review_note} Product data from Open Food Facts can be incomplete or wrong.{found.warning ? ` ${found.warning}` : ""}</Notice>}
              {found?.status === "unknown" && <Notice>{found.message} Just a name and size are enough; you can add label photos later.</Notice>}
              {found?.draft?.image_url && <img src={found.draft.image_url} alt="" style={{ width: 96, height: 96, objectFit: "contain", background: "#fff", borderRadius: 8, border: "1px solid var(--rule)" }} referrerPolicy="no-referrer" />}
              {found && <div className="xsmall muted num">Barcode {found.barcode}</div>}
              <Field label={mode === "nobarcode" ? "What is it?" : "Name"}><input className="input" required value={form.name || ""} onChange={(e) => setF({ name: e.target.value })} disabled={found?.status === "known"} placeholder={mode === "nobarcode" ? "e.g. Carrots, Rice (bulk)" : ""} /></Field>
              {mode !== "nobarcode" && found?.status !== "known" && (
                <div className="grid-2">
                  <Field label="Brand"><input className="input" value={form.brand || ""} onChange={(e) => setF({ brand: e.target.value })} /></Field>
                  <Field label="Pack size"><div className="row"><input className="input num" type="number" step="any" min={0} value={form.size} onChange={(e) => setF({ size: e.target.value })} /><select className="input" style={{ width: 80 }} value={form.size_unit} onChange={(e) => setF({ size_unit: e.target.value })}>{["g", "kg", "ml", "l", "each"].map((u) => <option key={u}>{u}</option>)}</select></div></Field>
                </div>
              )}
              {(found?.draft?.allergens?.length ?? 0) > 0 && <p className="small">Label allergens (from community data): <strong>{found!.draft.allergens.join(", ")}</strong>. Check the pack.</p>}
              <div className="grid-2">
                <Field label="How many"><div className="row"><input className="input num" type="number" min={0} step="any" value={form.quantity} onChange={(e) => setF({ quantity: e.target.value })} />
                  <select className="input" style={{ width: 96 }} value={form.unit} onChange={(e) => setF({ unit: e.target.value })}>{(mode === "nobarcode" ? ["g", "kg", "ml", "l", "each"] : ["pack", "each", "g", "ml"]).map((u) => <option key={u}>{u}</option>)}</select></div></Field>
                <Field label="Where"><select className="input" value={form.location} onChange={(e) => setF({ location: e.target.value })}>{LOCATIONS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}</select></Field>
              </div>
              <div className="grid-2">
                <Field label="Use by" hint="As printed, if it has one"><input className="input" type="date" value={form.use_by} onChange={(e) => setF({ use_by: e.target.value })} /></Field>
                <Field label="Best before"><input className="input" type="date" value={form.best_before} onChange={(e) => setF({ best_before: e.target.value })} /></Field>
              </div>
              <details>
                <summary className="btn ghost small">Shop and price (optional)</summary>
                <div className="grid-2" style={{ marginTop: 8 }}>
                  <Field label="Shop"><input className="input" value={form.store} onChange={(e) => setF({ store: e.target.value })} list="shops" /></Field>
                  <Field label="Price paid (£)"><input className="input num" type="number" min={0} step="0.01" value={form.price} onChange={(e) => setF({ price: e.target.value })} /></Field>
                </div>
                <datalist id="shops">{["Sainsbury's", "Tesco", "Lidl", "Aldi", "M&S", "Asda", "Co-op", "Waitrose", "Morrisons"].map((s) => <option key={s} value={s} />)}</datalist>
              </details>
              <p className="xsmall muted">Nothing is recorded as bought until you press Add.</p>
              <div className="row-wrap">
                <button className="btn primary big" disabled={busy}>{form.confirm_duplicate ? "Add again anyway" : "Add to pantry"}</button>
                <button type="button" className="btn ghost" onClick={() => { setFound(null); setError(null); setForm({}); }}>Cancel</button>
              </div>
            </form>
          )}
          {!showForm && <p className="muted small">Point the camera at a barcode. In batch mode the camera stays on so you can catalogue a whole cupboard.</p>}
        </div>
      </div>
    </>
  );
}
