import { FormEvent, useState } from "react";
import Icon from "../components/Icon";
import { Field, Notice, Sheet, useApi, useToast } from "../components/ui";
import { api, errorText } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate } from "../lib/format";

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function Settings() {
  const { me, signOut } = useAuth();
  const isAdmin = me?.user.role === "admin";
  return (
    <>
      <div className="page-head"><div><div className="eyebrow">{me?.household.name}</div><h1>Settings</h1></div><button className="btn" onClick={signOut}>Sign out</button></div>
      <div className="two-col" style={{ alignItems: "start" }}>
        <div className="stack"><People /><Pets /></div>
        <div className="stack">
          <Users isAdmin={isAdmin} />
          {isAdmin && <Tokens />}
          {isAdmin && <Admin />}
          <Password />
        </div>
      </div>
    </>
  );
}

function People() {
  const { data, reload } = useApi<any[]>("/people");
  const [edit, setEdit] = useState<any>(null);
  return (
    <section className="panel">
      <div className="spread"><h2>People</h2><button className="btn small" onClick={() => setEdit({ name: "", kind: "child", allergies: [], dislikes: [], dietary_preferences: [] })}><Icon name="plus" size={16} />Add</button></div>
      <p className="xsmall muted">Children are profiles for portions and intake, not logins.</p>
      <ul className="list" style={{ marginTop: 8 }}>
        {(data || []).map((p) => (
          <li key={p.id}><button className="list-row" onClick={() => setEdit(p)} style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }}>
            <span className="grow"><span className="title">{p.name}</span> <span className="tag plain">{p.kind}</span>
              <span className="meta" style={{ display: "block" }}>{[p.allergies.length ? `Allergies: ${p.allergies.join(", ")}` : "", p.dietary_preferences.length ? p.dietary_preferences.join(", ") : "", p.dislikes.length ? `Dislikes ${p.dislikes.join(", ")}` : ""].filter(Boolean).join(" · ") || "No allergies or preferences recorded"}</span></span>
            <Icon name="right" />
          </button></li>
        ))}
      </ul>
      <PersonSheet person={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />
    </section>
  );
}

function PersonSheet({ person, onClose, onSaved }: { person: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const cur = f || (person ? { ...person, allergies: person.allergies.join(", "), dislikes: person.dislikes.join(", "), dietary_preferences: person.dietary_preferences.join(", ") } : null);
  if (!person) return null;
  async function save(e: FormEvent) {
    e.preventDefault();
    const body = { name: cur.name, kind: cur.kind, default_portion: cur.default_portion ? parseFloat(cur.default_portion) : null, allergies: list(cur.allergies), dislikes: list(cur.dislikes), dietary_preferences: list(cur.dietary_preferences), notes: cur.notes || null };
    try { await (person.id ? api(`/people/${person.id}`, { method: "PATCH", body }) : api("/people", { body })); setF(null); onSaved(); } catch (err) { setError(errorText(err)); }
  }
  return (
    <Sheet open={!!person} onClose={() => { setF(null); onClose(); }} title={person.id ? person.name : "Add a person"}>
      <form className="stack" onSubmit={save}>
        <div className="grid-2">
          <Field label="Name"><input className="input" required value={cur.name} onChange={(e) => setF({ ...cur, name: e.target.value })} /></Field>
          <Field label="Profile"><select className="input" value={cur.kind} onChange={(e) => setF({ ...cur, kind: e.target.value })}><option value="adult">Adult</option><option value="child">Child</option></select></Field>
        </div>
        <Field label="Usual portion (servings)" hint="Used to suggest how much to cook; toddlers often eat about half a serving.">
          <input className="input num" type="number" min={0.25} max={5} step={0.25} value={cur.default_portion ?? (cur.kind === "child" ? 0.5 : 1)} onChange={(e) => setF({ ...cur, default_portion: e.target.value })} />
        </Field>
        <Field label="Allergies" hint="Comma separated. Used to flag possible issues; never a guarantee."><input className="input" value={cur.allergies} onChange={(e) => setF({ ...cur, allergies: e.target.value })} /></Field>
        <Field label="Dietary preferences" hint="e.g. vegetarian, halal"><input className="input" value={cur.dietary_preferences} onChange={(e) => setF({ ...cur, dietary_preferences: e.target.value })} /></Field>
        <Field label="Dislikes"><input className="input" value={cur.dislikes} onChange={(e) => setF({ ...cur, dislikes: e.target.value })} /></Field>
        {error && <Notice tone="error">{error}</Notice>}
        <button className="btn primary">Save</button>
      </form>
    </Sheet>
  );
}

function Pets() {
  const { data, reload } = useApi<any[]>("/pets");
  const [edit, setEdit] = useState<any>(null);
  const toast = useToast();
  async function save(e: FormEvent) {
    e.preventDefault();
    await (edit.id ? api(`/pets/${edit.id}`, { method: "PATCH", body: edit }) : api("/pets", { body: edit }));
    toast("Saved"); setEdit(null); reload();
  }
  return (
    <section className="panel">
      <div className="spread"><h2>Pets</h2><button className="btn small" onClick={() => setEdit({ name: "", species: "dog" })}><Icon name="plus" size={16} />Add</button></div>
      <p className="xsmall muted">Pet food has its own stock and purchase history and never counts towards human nutrition. Larder doesn't give feeding advice; record your vet's plan here if you like.</p>
      <ul className="list" style={{ marginTop: 8 }}>{(data || []).map((p) => <li key={p.id}><button className="list-row" onClick={() => setEdit(p)} style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }}><Icon name="paw" /><span className="grow"><span className="title">{p.name}</span><span className="meta" style={{ display: "block" }}>{p.feeding_plan || "No feeding plan recorded"}</span></span></button></li>)}</ul>
      <Sheet open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? edit.name : "Add a pet"}>
        {edit && <form className="stack" onSubmit={save}>
          <Field label="Name"><input className="input" required value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <Field label="Feeding plan (as agreed with your vet)"><textarea className="input" value={edit.feeding_plan || ""} onChange={(e) => setEdit({ ...edit, feeding_plan: e.target.value })} /></Field>
          <Field label="Plan source"><input className="input" value={edit.feeding_plan_source || ""} onChange={(e) => setEdit({ ...edit, feeding_plan_source: e.target.value })} placeholder="e.g. Vet, March 2026" /></Field>
          <button className="btn primary">Save</button>
        </form>}
      </Sheet>
    </section>
  );
}

function Users({ isAdmin }: { isAdmin: boolean }) {
  const { data, reload } = useApi<any[]>("/users");
  const [add, setAdd] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  async function save(e: FormEvent) {
    e.preventDefault();
    try { await api("/users", { body: add }); setAdd(null); reload(); } catch (err) { setError(errorText(err)); }
  }
  return (
    <section className="panel">
      <div className="spread"><h2>Adults who can sign in</h2>{isAdmin && <button className="btn small" onClick={() => setAdd({ email: "", display_name: "", password: "", role: "member" })}><Icon name="plus" size={16} />Invite</button>}</div>
      <ul className="list" style={{ marginTop: 8 }}>{(data || []).map((u) => <li key={u.id} className="list-row"><span className="grow"><span className="title">{u.display_name}</span><span className="meta" style={{ display: "block" }}>{u.email} · {u.role}{u.last_login_at ? ` · last in ${fmtDate(u.last_login_at)}` : ""}</span></span></li>)}</ul>
      <Sheet open={!!add} onClose={() => setAdd(null)} title="Add an adult">
        {add && <form className="stack" onSubmit={save}>
          <Field label="Name"><input className="input" required value={add.display_name} onChange={(e) => setAdd({ ...add, display_name: e.target.value })} /></Field>
          <Field label="Email"><input className="input" type="email" required value={add.email} onChange={(e) => setAdd({ ...add, email: e.target.value })} /></Field>
          <Field label="Temporary password" hint="At least 10 characters; they can change it after signing in."><input className="input" type="password" minLength={10} required value={add.password} onChange={(e) => setAdd({ ...add, password: e.target.value })} /></Field>
          {error && <Notice tone="error">{error}</Notice>}
          <button className="btn primary">Add</button>
        </form>}
      </Sheet>
    </section>
  );
}

function Tokens() {
  const { data, reload } = useApi<any[]>("/tokens");
  const scopes = useApi<Record<string, string>>("/tokens/scopes");
  const people = useApi<any[]>("/people");
  const [form, setForm] = useState<any>(null);
  const [created, setCreated] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  async function create(e: FormEvent) {
    e.preventDefault();
    try { const { agent: _agent, ...body } = form; setCreated(await api("/tokens", { body })); setForm(null); reload(); } catch (err) { setError(errorText(err)); }
  }
  const toggle = (k: string, v: string) => setForm({ ...form, [k]: form[k].includes(v) ? form[k].filter((x: string) => x !== v) : [...form[k], v] });
  return (
    <section className="panel">
      <div className="spread"><h2>Connected apps</h2>
        <div className="row-wrap">
          <button className="btn small" onClick={() => setForm({ name: "AI agent (MCP)", agent: true, person_ids: (people.data || []).filter((p) => p.kind === "adult").map((p) => p.id),
            scopes: ["recipes:read", "recipes:write", "pantry:read", "pantry:write", "plan:read", "plan:write", "receipts:write", "intake:read", "intake:write", "health:read", "health:write"] })}><Icon name="idea" size={16} />AI agent</button>
          <button className="btn small" onClick={async () => { try { setCreated(await api("/tokens", { body: { name: "Calendar feed", scopes: ["calendar:read"], person_ids: [] } })); reload(); } catch (e) { setError(errorText(e)); } }}><Icon name="plan" size={16} />Calendar feed</button>
          <button className="btn small" onClick={() => setForm({ name: "Activity app", scopes: ["intake:read"], person_ids: [] })}><Icon name="plus" size={16} />New token</button>
        </div>
      </div>
      <p className="xsmall muted">Read-only tokens let another app poll intake via <code>/api/v1/people/&lt;id&gt;/intake/export?updated_since=…</code>. Children's intake is only shared with the explicit children scope.</p>
      {created && <Notice tone="warn">Copy this token now; it won't be shown again:<pre className="raw" style={{ userSelect: "all", margin: "6px 0 0", wordBreak: "break-all" }}>{created.token}</pre>
        {created.scopes.includes("calendar:read") && (
          <div className="small" style={{ marginTop: 8 }}>
            Subscribe to meals and who's out in your phone's calendar (iPhone: tap the link; Google Calendar: “From URL”):
            <pre className="raw" style={{ userSelect: "all", margin: "6px 0 0", wordBreak: "break-all" }}>{`webcal://${window.location.host}/api/v1/calendar.ics?token=${created.token}`}</pre>
            Anyone with this link can see your meal plan and who's out, so share it carefully; revoke it here to stop it. Medicines and intake are never included.
          </div>
        )}
        {created.scopes.includes("plan:write") && (
          <div className="small" style={{ marginTop: 8 }}>
            To connect an AI agent over MCP, point it at <code>{window.location.origin}/mcp</code> and send the token as a bearer header. For example, in Claude Code:
            <pre className="raw" style={{ userSelect: "all", margin: "6px 0 0", wordBreak: "break-all" }}>{`claude mcp add --transport http larder ${window.location.origin}/mcp --header "Authorization: Bearer ${created.token}"`}</pre>
            See docs/MCP.md for desktop apps and local (stdio) use.
          </div>
        )}
      </Notice>}
      <ul className="list" style={{ marginTop: 8 }}>
        {(data || []).map((t) => (
          <li key={t.id} className="list-row">
            <span className="grow"><span className="title">{t.name}</span> {t.revoked_at && <span className="tag tomato">revoked</span>}
              <span className="meta" style={{ display: "block" }}>{t.scopes.join(", ")} · lrd_{t.prefix}_… · {t.last_used_at ? `used ${fmtDate(t.last_used_at)}` : "never used"}</span></span>
            {!t.revoked_at && <><button className="btn small" onClick={async () => { setCreated(await api(`/tokens/${t.id}/rotate`, { method: "POST" })); reload(); }}>Rotate</button>
              <button className="btn small danger" onClick={async () => { await api(`/tokens/${t.id}`, { method: "DELETE" }); reload(); }}>Revoke</button></>}
          </li>
        ))}
      </ul>
      <Sheet open={!!form} onClose={() => setForm(null)} title="New API token">
        {form && <form className="stack" onSubmit={create}>
          <Field label="Name"><input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          {form.agent && <p className="xsmall muted">An AI agent can read and plan, add and use stock, draft recipes and changes, and review receipts. Drafts and receipts still need your go-ahead before they're saved or applied. Children's intake isn't shared unless you tick the children scope.</p>}
          <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}><legend className="eyebrow">Allowed</legend>
            {Object.entries(scopes.data || {}).map(([k, d]) => <label key={k} className="row small"><input type="checkbox" checked={form.scopes.includes(k)} onChange={() => toggle("scopes", k)} /> <code>{k}</code> <span className="muted">{d}</span></label>)}</fieldset>
          <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}><legend className="eyebrow">Whose intake</legend>
            {(people.data || []).map((p) => <label key={p.id} className="row small"><input type="checkbox" checked={form.person_ids.includes(p.id)} onChange={() => toggle("person_ids", p.id)} /> {p.name} {p.kind === "child" && <span className="tag plain">child</span>}</label>)}</fieldset>
          {error && <Notice tone="error">{error}</Notice>}
          <button className="btn primary">Create token</button>
        </form>}
      </Sheet>
    </section>
  );
}

function Admin() {
  const status = useApi<any>("/admin/status");
  const backups = useApi<any[]>("/admin/backups");
  const jobs = useApi<any[]>("/admin/jobs?status=failed&limit=10");
  const toast = useToast();
  const s = status.data;
  return (
    <section className="panel stack-sm">
      <h2>Server</h2>
      {s && <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        <li>AI help: {s.ai.enabled ? `on (${s.ai.model}); ${s.ai.calls_30d} calls, about $${s.ai.est_cost_usd_30d} in 30 days` : "off — manual entry still works"}</li>
        <li>Barcode lookup: {s.barcode_lookup ? "Open Food Facts" : "off"}</li>
        <li>Backups: {s.backups.enabled ? `nightly${s.backups.encrypted ? ", encrypted" : ", not encrypted"}, keeping ${s.backups.keep}` : "off"}{s.backups.latest ? `; latest ${fmtDate(s.backups.latest.created_at)}` : ""}</li>
      </ul>}
      <div className="row-wrap">
        <button className="btn small" onClick={async () => { await api("/admin/backups", { method: "POST" }); toast("Backup started"); setTimeout(backups.reload, 4000); }}><Icon name="download" size={16} />Back up now</button>
        <a className="btn small" href="/api/v1/export"><Icon name="download" size={16} />Export everything (zip)</a>
      </div>
      {(backups.data || []).slice(0, 3).map((b) => <div key={b.name} className="xsmall"><a href={`/api/v1/admin/backups/${b.name}`}>{b.name}</a> <span className="muted">{Math.round(b.size / 1024)} KB</span></div>)}
      {(jobs.data || []).length > 0 && (
        <div>
          <div className="eyebrow" style={{ margin: "8px 0 4px" }}>Failed jobs</div>
          {(jobs.data || []).map((j) => <div key={j.id} className="row small"><span className="grow">{j.kind}: {j.error}</span><button className="btn small" onClick={async () => { await api(`/admin/jobs/${j.id}/retry`, { method: "POST" }); jobs.reload(); }}>Retry</button></div>)}
        </div>
      )}
    </section>
  );
}

function Password() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const toast = useToast();
  async function save(e: FormEvent) {
    e.preventDefault();
    try { await api("/auth/password", { body: { current_password: cur, new_password: next } }); toast("Password changed; other devices were signed out"); setCur(""); setNext(""); } catch (err) { toast(errorText(err)); }
  }
  return (
    <form className="panel stack-sm" onSubmit={save}>
      <h2>Your password</h2>
      <div className="grid-2">
        <Field label="Current"><input className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></Field>
        <Field label="New"><input className="input" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required /></Field>
      </div>
      <div><button className="btn small">Change password</button></div>
    </form>
  );
}
