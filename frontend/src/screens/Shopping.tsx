import { FormEvent, useState } from "react";
import Icon from "../components/Icon";
import { Empty, ErrorNote, Loading, Segmented, Sheet, useApi, useToast } from "../components/ui";
import { api, errorText } from "../lib/api";
import { addDays, isoToday } from "../lib/format";

export default function Shopping() {
  const [group, setGroup] = useState<"aisle" | "shop">("aisle");
  const { data, error, loading, reload, setData } = useApi<any>(`/shopping-list?group_by=${group}`, [group]);
  const [name, setName] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const toast = useToast();

  async function patch(item: any, body: any) {
    // optimistic tick
    if ("checked" in body && data) setData({ ...data, groups: data.groups.map((g: any) => ({ ...g, items: g.items.map((i: any) => (i.id === item.id ? { ...i, ...body } : i)) })) });
    try { await api(`/shopping-list/items/${item.id}`, { method: "PATCH", body }); if (!("checked" in body)) reload(); } catch (e) { toast(errorText(e)); reload(); }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await api("/shopping-list/items", { body: { name: name.trim() } });
    setName(""); reload();
  }
  const checked = (data?.groups || []).flatMap((g: any) => g.items).filter((i: any) => i.checked).length;

  return (
    <>
      <div className="page-head">
        <div><div className="eyebrow">Planned meals minus what's in</div><h1>Shopping list</h1></div>
        <div className="row-wrap">
          <Segmented label="Group by" value={group} onChange={setGroup} options={[{ value: "aisle", label: "By aisle" }, { value: "shop", label: "By shop" }]} />
          {checked > 0 && <button className="btn" onClick={async () => { await api("/shopping-list/clear-checked", { method: "POST" }); reload(); }}>Clear {checked} ticked</button>}
        </div>
      </div>
      <form className="row" onSubmit={add} style={{ marginBottom: 16, maxWidth: 520 }}>
        <input className="input" placeholder="Add something else" aria-label="Add an item" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn"><Icon name="plus" />Add</button>
      </form>
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={6} />}
      {data && data.count === 0 && <Empty icon="cart" title="Nothing to buy">Plan some meals and anything you don't have enough of will appear here.</Empty>}
      {data?.generated?.unresolved?.length > 0 && (
        <p className="small muted">Not included automatically: {data.generated.unresolved.map((u: any) => `${u.name} (${u.reason})`).join("; ")}.</p>
      )}
      <div className="two-col" style={{ alignItems: "start" }}>
        {(data?.groups || []).map((g: any) => (
          <section key={g.name}>
            <h2 style={{ fontSize: "1.1875rem", margin: "8px 0" }}>{g.name}</h2>
            <ul className="list">
              {g.items.map((i: any) => (
                <li key={i.id} className="list-row" style={{ opacity: i.checked ? 0.55 : 1 }}>
                  <input type="checkbox" checked={i.checked} onChange={(e) => patch(i, { checked: e.target.checked })} aria-label={`Got ${i.name}`} style={{ width: 24, height: 24, accentColor: "var(--herb)" }} />
                  <button onClick={() => setDetail(i)} style={{ all: "unset", cursor: "pointer", flex: 1, minWidth: 0 }}>
                    <span className="title" style={{ display: "block", textDecoration: i.checked ? "line-through" : undefined }}>{i.product_name || i.name}</span>
                    <span className="meta">{i.reason}</span>
                  </button>
                  <span className="qty">{i.display || ""}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <ItemSheet item={detail} onClose={() => setDetail(null)} onSaved={() => { setDetail(null); reload(); }} />
    </>
  );
}

function ItemSheet({ item, onClose, onSaved }: { item: any; onClose: () => void; onSaved: () => void }) {
  const products = useApi<any[]>(item?.food_id ? `/products?food_id=${item.food_id}` : null, [item?.id]);
  const prices = useApi<any>(item?.food_id ? `/prices/history?foodId=${item.food_id}` : null, [item?.id]);
  const [qty, setQty] = useState<string>("");
  const [shop, setShop] = useState<string>("");
  if (!item) return null;
  const save = async (body: any) => { await api(`/shopping-list/items/${item.id}`, { method: "PATCH", body }); setQty(""); setShop(""); onSaved(); };
  const ps = prices.data?.summary;
  return (
    <Sheet open={!!item} onClose={onClose} title={item.name}>
      <div className="stack">
        <p className="small muted">{item.reason}</p>
        {ps && <p className="small">Usually £{(ps.usually_paid / 100).toFixed(2)}/{ps.standard_unit}; lowest £{(ps.lowest / 100).toFixed(2)}/{ps.standard_unit} ({ps.observations} purchases).</p>}
        <div className="grid-2">
          <label className="field"><span>Amount ({item.unit || "items"})</span><input className="input num" type="number" min={0} step="any" placeholder={item.quantity ?? ""} value={qty} onChange={(e) => setQty(e.target.value)} /></label>
          <label className="field"><span>Shop</span><input className="input" placeholder={item.shop || "Any"} value={shop} onChange={(e) => setShop(e.target.value)} /></label>
        </div>
        {(products.data || []).length > 0 && (
          <label className="field"><span>Brand / product</span>
            <select className="input" value={item.product_id || ""} onChange={(e) => save({ product_id: e.target.value || null })}>
              <option value="">Any {item.name.toLowerCase()}</option>
              {(products.data || []).map((p) => <option key={p.id} value={p.id}>{p.brand ? `${p.brand} ` : ""}{p.name}{p.package_quantity ? ` (${p.package_quantity} ${p.package_unit})` : ""}</option>)}
            </select>
          </label>
        )}
        <div className="row-wrap">
          <button className="btn primary" onClick={() => save({ ...(qty ? { quantity: parseFloat(qty) } : {}), ...(shop ? { shop } : {}) })}>Save</button>
          {item.origin !== "manual" && <button className="btn" onClick={() => save({ snoozed_until: addDays(isoToday(), 7) })}>Snooze a week</button>}
          <button className="btn danger" onClick={async () => { await api(`/shopping-list/items/${item.id}`, { method: "DELETE" }); onSaved(); }}>{item.origin === "manual" ? "Delete" : "Dismiss"}</button>
        </div>
      </div>
    </Sheet>
  );
}
