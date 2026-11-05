import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import { Empty, ErrorNote, Loading, Segmented, useApi, useToast } from "../components/ui";
import { api, errorText } from "../lib/api";
import { addDays, daysUntil, fmtDay, money, perUnit } from "../lib/format";

type Week = { week: string; food: number; pet: number; health: number; total: number };
const SERIES = [
  { key: "food", label: "Food", color: "var(--series-1)" },
  { key: "pet", label: "Pet food", color: "var(--series-2)" },
  { key: "health", label: "Medicines & vitamins", color: "var(--series-3)" },
] as const;

export default function Insights() {
  const [weeks, setWeeks] = useState("12");
  const { data, error, loading, reload } = useApi<any>(`/insights?weeks=${weeks}`, [weeks]);
  const soon = (data?.forecast.forecasts || []).filter((f: any) => !f.stock_covers_usual_buy && (daysUntil(f.earliest) ?? 99) <= 7);

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Household</div><h1>Insights</h1></div>
        <Segmented label="Period" value={weeks} onChange={setWeeks}
          options={[{ value: "8", label: "8 weeks" }, { value: "12", label: "12 weeks" }, { value: "26", label: "6 months" }]} />
      </div>
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={8} />}
      {data && (
        <>
          <dl className="facts insight-facts">
            <div><dt>Spent</dt><dd className="hero-num">{money(data.spending.total_pence)}</dd>
              <span className="small muted">{data.spending.average_week_pence !== null ? `${money(data.spending.average_week_pence)} a week` : "No prices yet"}
                {data.spending.previous_period_pence ? ` · ${trendWord(data.spending.total_pence, data.spending.previous_period_pence)} the ${weeks} weeks before` : ""}</span></div>
            <div><dt>Thrown away</dt><dd className="hero-num">{money(data.usage.wasted_value_pence)}</dd>
              <span className="small muted">{data.usage.waste_share !== null ? `${Math.round(data.usage.waste_share * 1000) / 10}% of what was used, by value` : "Nothing priced yet"}</span></div>
            <div><dt>Likely to run out this week</dt><dd className="hero-num">{soon.length}</dd>
              <span className="small muted">{soon.slice(0, 3).map((f: any) => f.name).join(", ") || "Nothing expected"}</span></div>
          </dl>

          <div className="insights-grid">
            <div className="stack">
            <section className="panel">
              <div className="section-title" style={{ marginTop: 0 }}><h2>Running out</h2><Link to="/shopping" className="small">Shopping list</Link></div>
              <Forecasts data={data.forecast} reload={reload} />
            </section>
            <section className="panel">
              <div className="section-title" style={{ marginTop: 0 }}><h2>Thrown away</h2></div>
              {data.usage.top_wasted.length === 0 ? <p className="muted small">Nothing recorded as thrown away in this period.</p> : (
                <RankBars rows={data.usage.top_wasted} />)}
              {data.usage.unpriced_waste_events > 0 && <p className="xsmall muted">{data.usage.unpriced_waste_events} more had no price recorded.</p>}
              <h3 className="eyebrow" style={{ marginTop: 20 }}>Used most often</h3>
              <ul className="list compact">
                {data.usage.top_used.slice(0, 6).map((u: any) => (
                  <li key={u.name} className="row" style={{ justifyContent: "space-between", padding: "6px 0" }}>
                    <span>{u.name}</span><span className="small muted num">{u.quantity_display} · {u.events}×</span></li>))}
              </ul>
              <p className="xsmall muted" style={{ marginTop: 12 }}>{data.usage.basis}</p>
            </section>

            </div>
            <div className="stack">

            <section className="panel">
              <div className="section-title" style={{ marginTop: 0 }}><h2>Spending by week</h2></div>
              <SpendChart weeks={data.spending.weeks} />
              {data.spending.by_store.length > 0 && (
                <ul className="list compact" style={{ marginTop: 12 }}>
                  {data.spending.by_store.map((s: any) => (
                    <li key={s.store} className="row" style={{ justifyContent: "space-between", padding: "6px 0" }}>
                      <span>{s.store}</span><span className="num">{money(s.pence)}</span></li>))}
                </ul>)}
              <p className="xsmall muted" style={{ marginTop: 12 }}>{data.spending.basis}
                {data.spending.unpriced_purchases ? ` ${data.spending.unpriced_purchases} purchases had no price and aren't counted.` : ""}</p>
            </section>

            <section className="panel">
              <div className="section-title" style={{ marginTop: 0 }}><h2>What you pay</h2></div>
              {data.price_trends.length === 0 ? <p className="muted small">Price trends appear once you've bought something at least twice, a fortnight apart.</p> : (
                <div className="price-grid">{data.price_trends.slice(0, 6).map((t: any) => <PriceTrend key={t.name + t.standard_unit} t={t} />)}</div>)}
              <p className="xsmall muted" style={{ marginTop: 12 }}>Prices paid per kg, litre or item, so different pack sizes compare fairly. Change is the latest price against the typical earlier price.</p>
            </section>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function trendWord(now: number, before: number) {
  const pct = Math.round(((now - before) / before) * 100);
  return pct === 0 ? "the same as" : pct > 0 ? `${pct}% more than` : `${-pct}% less than`;
}

/* ------------------------------------------------------------------ forecasts */
function Forecasts({ data, reload }: { data: any; reload: () => void }) {
  const toast = useToast();
  const [adding, setAdding] = useState<string | null>(null);
  const rows: any[] = data.forecasts;
  const span = 28;
  const today = new Date().toLocaleDateString("en-CA");
  if (!rows.length) return (
    <Empty icon="clock" title="Not enough history yet">Forecasts appear after a couple of weeks of recording what you use, or a few purchases of the same thing.</Empty>
  );
  async function add(f: any) {
    setAdding(f.food_id);
    try {
      await api("/shopping-list/items", { body: { name: f.name, food_id: f.food_id } });
      toast(`${f.name} added to the shopping list`);
      reload();
    } catch (e) { toast(errorText(e)); } finally { setAdding(null); }
  }
  const pos = (iso: string) => Math.min(100, Math.max(0, ((daysUntil(iso) ?? 0) / span) * 100));
  return (
    <>
      <div className="fc-axis" aria-hidden="true">
        <span style={{ left: "0%" }}>Today</span><span style={{ left: "25%" }}>{fmtDay(addDays(today, 7))}</span>
        <span style={{ left: "50%" }}>{fmtDay(addDays(today, 14))}</span><span style={{ left: "75%" }}>{fmtDay(addDays(today, 21))}</span>
      </div>
      <ul className="list fc-list">
        {rows.map((f) => {
          const a = pos(f.earliest), b = pos(f.latest), m = pos(f.run_out);
          const when = f.out_now ? "Out now" : f.earliest === f.latest ? `Around ${fmtDay(f.earliest)}` : `${fmtDay(f.earliest)} – ${fmtDay(f.latest)}`;
          return (
            <li key={f.food_id} className="fc-row">
              <div className="fc-head">
                <span className="title">{f.name}{f.is_pet_food && <span className="tag plain" style={{ marginLeft: 6 }}>Pet</span>}</span>
                <span className="small num">{f.method === "purchases" ? "Usually rebuy " : ""}{when}</span>
              </div>
              <div className="fc-track" role="img" aria-label={`${f.name}: ${f.method === "purchases" ? "usually rebought" : "likely to run out"} ${when}, ${f.confidence} confidence`}>
                {[25, 50, 75].map((x) => <i key={x} className="fc-grid" style={{ left: `${x}%` }} />)}
                <span className={`fc-range ${f.confidence}`} style={{ left: `${a}%`, width: `${Math.max(1.5, b - a)}%` }} />
                <span className="fc-dot" style={{ left: `${m}%` }} />
              </div>
              <div className="meta small">{f.basis}.</div>
              <div className="row" style={{ justifyContent: "space-between", marginTop: 4 }}>
                <span className={`tag ${f.confidence === "high" ? "herb" : f.confidence === "medium" ? "plain" : "ochre"}`}>{f.confidence} confidence</span>
                {f.on_shopping_list ? <span className="xsmall muted row"><Icon name="check" size={14} />On the list</span>
                  : <button className="btn small ghost" disabled={adding === f.food_id} onClick={() => add(f)}><Icon name="plus" size={16} />Add to list</button>}
              </div>
            </li>);
        })}
      </ul>
      <p className="xsmall muted">{data.basis}{data.learning.length ? ` Still learning: ${data.learning.slice(0, 5).join(", ")}${data.learning.length > 5 ? ` and ${data.learning.length - 5} more` : ""}.` : ""}</p>
    </>
  );
}

/* ------------------------------------------------------------------ spending chart */
function useWidth<T extends HTMLElement>(fallback: number) {
  const [el, setEl] = useState<T | null>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w] as const;
}

const shortDay = (iso: string) => { const d = new Date(iso + "T12:00:00"); return `${d.getDate()} ${d.toLocaleDateString("en-GB", { month: "short" })}`; };

function SpendChart({ weeks }: { weeks: Week[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth<HTMLDivElement>(600);
  const [table, setTable] = useState(false);
  const present = SERIES.filter((s) => weeks.some((w) => w[s.key] > 0));
  const max = Math.max(100, ...weeks.map((w) => w.total));
  const step = niceStep(max);
  const top = Math.ceil(max / step) * step;
  const H = 220, padL = 40, padB = 22, padT = 8;
  const bw = (W - padL) / weeks.length;
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / top);
  if (!weeks.some((w) => w.total)) return <p className="muted small">No priced purchases in this period. Applying receipts or adding prices when you scan fills this in.</p>;
  return (
    <div className="chart">
      <div className="legend" aria-hidden={table}>
        {present.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
        <button className="btn ghost small" style={{ marginLeft: "auto" }} onClick={() => setTable(!table)} aria-pressed={table}>{table ? "Chart" : "Table"}</button>
      </div>
      {table ? (
        <div className="ledger-wrap"><table className="ledger small">
          <thead><tr><th>Week of</th>{present.map((s) => <th key={s.key} className="money">{s.label}</th>)}<th className="money">Total</th></tr></thead>
          <tbody>{weeks.map((w) => <tr key={w.week}><td>{fmtDay(w.week)}</td>{present.map((s) => <td key={s.key} className="money">{money(w[s.key])}</td>)}<td className="money">{money(w.total)}</td></tr>)}</tbody>
        </table></div>
      ) : (
        <div style={{ position: "relative" }} ref={box}>
          <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="chart-svg" role="img" aria-label="Weekly spending, stacked by food, pet food and medicines" onMouseLeave={() => setHover(null)}>
            {Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step).map((v) => (
              <g key={v}><line x1={padL} x2={W} y1={y(v)} y2={y(v)} className="grid" /><text x={padL - 6} y={y(v) + 4} className="tick" textAnchor="end">£{v / 100}</text></g>))}
            {weeks.map((w, i) => {
              let acc = 0;
              const x = padL + i * bw + bw * 0.18, width = bw * 0.64;
              const segs = present.filter((s) => w[s.key] > 0);
              return (
                <g key={w.week} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0} aria-label={`Week of ${fmtDay(w.week)}: ${money(w.total)}`}>
                  <rect x={padL + i * bw} y={padT} width={bw} height={H - padT - padB} fill="transparent" />
                  {segs.map((s, k) => {
                    const v = w[s.key], y0 = y(acc), y1 = y(acc + v);
                    acc += v;
                    const isTop = k === segs.length - 1;
                    const h = Math.max(1, y0 - y1 - (isTop ? 0 : 2));
                    return isTop
                      ? <path key={s.key} d={topRounded(x, y1, width, h, Math.min(4, h, width / 2))} fill={s.color} opacity={hover === null || hover === i ? 1 : 0.45} />
                      : <rect key={s.key} x={x} y={y1 + 2} width={width} height={h} fill={s.color} opacity={hover === null || hover === i ? 1 : 0.45} />;
                  })}
                  {(i % Math.ceil(weeks.length / Math.max(2, Math.floor((W - padL) / 70))) === 0) && <text x={padL + i * bw + bw / 2} y={H - 6} className="tick" textAnchor="middle">{shortDay(w.week)}</text>}
                </g>);
            })}
            <line x1={padL} x2={W} y1={y(0)} y2={y(0)} className="axis" />
          </svg>
          {hover !== null && (
            <div className="chart-tip" style={{ left: `${((padL + (hover + 0.5) * bw) / W) * 100}%` }}>
              <strong>Week of {fmtDay(weeks[hover].week)}</strong>
              {present.map((s) => <div key={s.key} className="row" style={{ justifyContent: "space-between", gap: 12 }}><span className="row" style={{ gap: 6 }}><i style={{ background: s.color }} />{s.label}</span><span className="num">{money(weeks[hover][s.key])}</span></div>)}
              <div className="row" style={{ justifyContent: "space-between", borderTop: "1px solid var(--rule)", paddingTop: 4 }}><span>Total</span><span className="num">{money(weeks[hover].total)}</span></div>
            </div>)}
        </div>
      )}
    </div>
  );
}

function niceStep(max: number) {
  const raw = max / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
}

function topRounded(x: number, y: number, w: number, h: number, r: number) {
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/* ------------------------------------------------------------------ waste bars */
function RankBars({ rows }: { rows: any[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value_pence || 0));
  return (
    <ul className="list compact rank-bars">
      {rows.map((r) => (
        <li key={r.name}>
          <div className="row" style={{ justifyContent: "space-between" }}><span>{r.name}</span><span className="num small">{r.value_pence ? money(r.value_pence) : "—"}</span></div>
          <div className="rank-track"><span style={{ width: `${((r.value_pence || 0) / max) * 100}%` }} /></div>
          <div className="xsmall muted">{r.quantity_display} over {r.events} {r.events === 1 ? "time" : "times"}</div>
        </li>))}
    </ul>
  );
}

/* ------------------------------------------------------------------ price sparkline */
function PriceTrend({ t }: { t: any }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts: any[] = t.points;
  const W = 220, H = 64, pad = 6;
  const { xs, ys } = useMemo(() => {
    const t0 = new Date(pts[0].date).getTime(), t1 = new Date(pts[pts.length - 1].date).getTime();
    const vals = pts.map((p) => p.unit_price);
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || hi * 0.1 || 1;
    return {
      xs: pts.map((p) => pad + ((new Date(p.date).getTime() - t0) / Math.max(1, t1 - t0)) * (W - pad * 2)),
      ys: pts.map((p) => pad + (1 - (p.unit_price - (lo - span * 0.15)) / (span * 1.3)) * (H - pad * 2)),
    };
  }, [pts]);
  const change = t.change_pct;
  const p = hover !== null ? pts[hover] : null;
  return (
    <div className="price-card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="title">{t.name}</span>
        <span className="num">{perUnit(t.latest, t.standard_unit)}</span>
      </div>
      <div style={{ position: "relative" }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="spark" role="img" aria-label={`${t.name} price per ${t.standard_unit}: latest ${perUnit(t.latest, t.standard_unit)}`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const x = ((e.clientX - r.left) / r.width) * W;
            let best = 0;
            xs.forEach((v, i) => { if (Math.abs(v - x) < Math.abs(xs[best] - x)) best = i; });
            setHover(best);
          }}>
          <polyline points={xs.map((x, i) => `${x},${ys[i]}`).join(" ")} className="spark-line" />
          {hover !== null && <line x1={xs[hover]} x2={xs[hover]} y1={0} y2={H} className="grid" />}
          <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r={4} className="spark-end" />
          {hover !== null && <circle cx={xs[hover]} cy={ys[hover]} r={4} className="spark-end" />}
        </svg>
        {p && <div className="chart-tip" style={{ left: `${(xs[hover!] / W) * 100}%`, top: -8 }}>
          <strong>{perUnit(p.unit_price, t.standard_unit)}</strong><div className="xsmall">{fmtDay(p.date)}{p.store ? ` · ${p.store}` : ""}{p.promotion ? ` · ${p.promotion}` : ""}</div></div>}
      </div>
      <div className="xsmall muted">
        {change === null || Math.abs(change) < 1 ? "Steady" : `${change > 0 ? "Up" : "Down"} ${Math.abs(change)}%`} · cheapest {perUnit(t.lowest.unit_price, t.standard_unit)} at {t.lowest.store || "a shop not recorded"}
        {t.stores.length > 1 ? ` · usually cheapest at ${t.stores[0].store}` : ""}
      </div>
    </div>
  );
}

