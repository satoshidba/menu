import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, errorText } from "../lib/api";
import { dateWording } from "../lib/format";
import Icon from "./Icon";

// ---- data hook ------------------------------------------------------------------------------------------
export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);
  const reload = useCallback(async () => {
    if (!path) return;
    const n = ++seq.current;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (n === seq.current) { setData(d); setError(null); }
    } catch (e) {
      if (n === seq.current) setError(errorText(e));
    } finally {
      if (n === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

// ---- toast -------------------------------------------------------------------------------------------------
type ToastMsg = { text: string; action?: { label: string; run: () => void } };
const ToastCtx = createContext<(m: ToastMsg | string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<ToastMsg | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((m: ToastMsg | string) => {
    setMsg(typeof m === "string" ? { text: m } : m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div aria-live="polite" role="status">
        {msg && (
          <div className="toast">
            <span>{msg.text}</span>
            {msg.action && <button onClick={() => { msg.action!.run(); setMsg(null); }}>{msg.action.label}</button>}
          </div>
        )}
      </div>
    </ToastCtx.Provider>
  );
}

// ---- sheet ----------------------------------------------------------------------------------------------------
export function Sheet({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; prev?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref} style={wide ? { width: "min(640px, 96vw)" } : undefined}>
        <div className="grabber" />
        <div className="spread" style={{ marginBottom: 16 }}>
          <h2 style={{ fontSize: "1.375rem" }}>{title}</h2>
          <button className="btn ghost icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
        </div>
        {children}
      </div>
    </>
  );
}

// ---- small pieces ----------------------------------------------------------------------------------------------
export function Field({ label, hint, children, error }: { label: string; hint?: string; children: ReactNode; error?: string | null }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && !error && <small className="hint">{hint}</small>}
      {error && <small className="hint" style={{ color: "var(--tomato-ink)" }}>{error}</small>}
    </label>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "error" | "ok"; children: ReactNode }) {
  const icon = tone === "error" || tone === "warn" ? "alert" : tone === "ok" ? "check" : "info";
  return <div className={`notice ${tone === "info" ? "" : tone}`} role={tone === "error" ? "alert" : undefined}><Icon name={icon} size={18} /><div>{children}</div></div>;
}

export function Empty({ icon = "jar", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return <div className="empty"><Icon name={icon} size={32} /><h3>{title}</h3>{children && <div className="small">{children}</div>}</div>;
}

export function Stepper({ value, onChange, step = 1, min = 0, max = 99, label, format }: { value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number; label: string; format?: (v: number) => string }) {
  return (
    <div className="stepper" role="group" aria-label={label}>
      <button type="button" onClick={() => onChange(Math.max(min, +(value - step).toFixed(3)))} aria-label={`Less ${label}`}><Icon name="minus" size={18} /></button>
      <output aria-live="polite">{format ? format(value) : value}</output>
      <button type="button" onClick={() => onChange(Math.min(max, +(value + step).toFixed(3)))} aria-label={`More ${label}`}><Icon name="plus" size={18} /></button>
    </div>
  );
}

export function DateBadge({ kind, date, label }: { kind?: string | null; date?: string | null; label?: string | null }) {
  if (!date) return null;
  const w = dateWording(kind, date, label);
  return <span className={`date-badge ${w.tone}`}>{w.text}</span>;
}

export function Media({ src, alt, ratio = "ratio-hero", focal, credit, creditHref, illustration, className, placeholder, eager }: {
  src?: string | null; alt: string; ratio?: string; focal?: [number, number]; credit?: string | null; creditHref?: string | null;
  illustration?: boolean; className?: string; placeholder?: string; eager?: boolean;
}) {
  if (!src) {
    return (
      <div className={`media placeholder ${ratio} ${className || ""}`}>
        <div><Icon name="image" size={30} /><div className="small" style={{ marginTop: 6 }}>{placeholder || "No photo yet"}</div></div>
      </div>
    );
  }
  const pos = focal ? `${Math.round(focal[0] * 100)}% ${Math.round(focal[1] * 100)}%` : "50% 50%";
  return (
    <figure className={`media ${ratio} ${className || ""}`} style={{ margin: 0 }}>
      <img src={src} alt={alt} loading={eager ? "eager" : "lazy"} decoding="async" fetchPriority={eager ? "high" : undefined} style={{ objectPosition: pos }} />
      {illustration && <span className="illus tag ochre">Illustration, not a photo</span>}
      {credit && (creditHref ? <a className="credit" href={creditHref} target="_blank" rel="noreferrer noopener">{credit}</a> : <span className="credit">{credit}</span>)}
    </figure>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>)}
    </div>
  );
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return <div aria-busy="true" aria-label="Loading" className="stack-sm">{Array.from({ length: lines }).map((_, i) => <div key={i} className="skeleton" style={{ height: i === 0 ? 28 : 18, width: `${90 - i * 12}%` }} />)}</div>;
}

export function ErrorNote({ error, retry }: { error: string; retry?: () => void }) {
  return <Notice tone="error">{error} {retry && <button className="btn small" style={{ marginLeft: 8 }} onClick={retry}>Try again</button>}</Notice>;
}

export const LOCATIONS = [
  { value: "cupboard", label: "Cupboard", icon: "cupboard" }, { value: "fridge", label: "Fridge", icon: "fridge" },
  { value: "freezer", label: "Freezer", icon: "freezer" }, { value: "other", label: "Other", icon: "jar" },
];
