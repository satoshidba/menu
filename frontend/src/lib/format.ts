// UK formats: en-GB dates, GBP, metric.
const dShort = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const dLong = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" });
const dNum = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "2-digit", year: "numeric" });
const tShort = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const gbp = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });

export const parseDate = (s?: string | null) => (s ? new Date(s.length === 10 ? s + "T12:00:00" : s) : null);
export const fmtDay = (s?: string | null) => { const d = parseDate(s); return d ? dShort.format(d) : "—"; };
export const fmtDayLong = (s?: string | null) => { const d = parseDate(s); return d ? dLong.format(d) : "—"; };
export const fmtDate = (s?: string | null) => { const d = parseDate(s); return d ? dNum.format(d) : "—"; };
export const fmtTime = (s?: string | null) => { const d = parseDate(s); return d ? tShort.format(d) : ""; };
export const money = (p?: number | null) => (p === null || p === undefined ? "—" : gbp.format(p / 100));
export const perUnit = (p?: number | null, unit?: string | null) => (p === null || p === undefined ? "—" : `${gbp.format(p / 100)}/${unit}`);
export const isoToday = () => new Date().toLocaleDateString("en-CA");
export const addDays = (iso: string, n: number) => { const d = parseDate(iso)!; d.setDate(d.getDate() + n); return d.toLocaleDateString("en-CA"); };
export const mondayOf = (iso: string) => { const d = parseDate(iso)!; const wd = (d.getDay() + 6) % 7; d.setDate(d.getDate() - wd); return d.toLocaleDateString("en-CA"); };

export function daysUntil(s?: string | null): number | null {
  const d = parseDate(s);
  if (!d) return null;
  const t = new Date(); t.setHours(12, 0, 0, 0);
  return Math.round((d.getTime() - t.getTime()) / 86400000);
}

export function dateWording(kind: string | null | undefined, s?: string | null, label?: string | null): { text: string; tone: "urgent" | "soon" | "later" } {
  const n = daysUntil(s);
  const base = label || (kind === "use_by" ? "Use by" : "Best before");
  if (n === null) return { text: "", tone: "later" };
  const when = n < 0 ? `${fmtDay(s)} (passed)` : n === 0 ? "today" : n === 1 ? "tomorrow" : fmtDay(s);
  const tone = kind === "use_by" ? (n <= 1 ? "urgent" : n <= 3 ? "soon" : "later") : n <= 0 ? "soon" : n <= 3 ? "soon" : "later";
  return { text: `${base} ${when}`, tone };
}

export const minutes = (m?: number | null) => (!m ? "—" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} hr${m % 60 ? ` ${m % 60} min` : ""}`);
export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
export const servings = (n?: number | null) => (n === null || n === undefined ? "—" : Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, "").replace(/\.$/, ""));
export const round = (v: number | null | undefined, dp = 0) => (v === null || v === undefined ? null : Number(v.toFixed(dp)));
