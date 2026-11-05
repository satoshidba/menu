import { FormEvent, useState } from "react";
import Icon from "../components/Icon";
import { Field, Notice } from "../components/ui";
import { api, errorText, setCsrf } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { needsSetup, refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [household, setHousehold] = useState("Our household");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = needsSetup
        ? await api("/setup", { body: { email, password, display_name: name, household_name: household } })
        : await api("/auth/login", { body: { email, password } });
      setCsrf(r.csrf_token);
      await refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 16 }}>
      <form onSubmit={submit} className="stack" style={{ width: "min(400px, 100%)" }}>
        <div className="wordmark" style={{ fontSize: "1.75rem" }}><Icon name="leaf" size={28} />Larder</div>
        <h1>{needsSetup ? "Set up your household" : "Welcome back"}</h1>
        <p className="muted">{needsSetup ? "You'll be the administrator. You can add the second adult, the children and the dog afterwards." : "Recipes, pantry and meals for the household."}</p>
        {error && <Notice tone="error">{error}</Notice>}
        {needsSetup && (
          <>
            <Field label="Household name"><input className="input" value={household} onChange={(e) => setHousehold(e.target.value)} required /></Field>
            <Field label="Your name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" /></Field>
          </>
        )}
        <Field label="Email"><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" /></Field>
        <Field label="Password" hint={needsSetup ? "At least 10 characters." : undefined}>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={needsSetup ? 10 : 1}
            autoComplete={needsSetup ? "new-password" : "current-password"} />
        </Field>
        <button className="btn primary big" style={{ width: "100%" }} disabled={busy}>{busy ? "One moment…" : needsSetup ? "Create household" : "Sign in"}</button>
      </form>
    </div>
  );
}
