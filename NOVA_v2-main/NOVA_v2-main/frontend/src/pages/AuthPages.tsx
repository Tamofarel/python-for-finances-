import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { Orb } from "../components/Orb";
import { Field, Notice } from "../components/ui";
import { useSession } from "../store/session";

function AuthFrame({ title, intro, children }: { title: string; intro: string; children: React.ReactNode }) {
  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <Orb presence="idle" size={44} />
          <div>
            <h1>{title}</h1>
            <p className="muted">{intro}</p>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SetupPage() {
  const accept = useSession((s) => s.accept);
  const [form, setForm] = useState({ username: "", display_name: "", password: "", confirm: "", setup_code: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (form.password !== form.confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      accept(await api.auth.setup({ username: form.username, display_name: form.display_name, password: form.password, setup_code: form.setup_code }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame title="Set up NOVA" intro="Create the owner account for this computer. NOVA stores everything locally.">
      <form onSubmit={submit} className="stack">
        <Field label="Setup code" hint="Printed in the terminal window where the NOVA backend started.">
          <input value={form.setup_code} onChange={set("setup_code")} required inputMode="numeric" autoComplete="one-time-code" />
        </Field>
        <Field label="Username" hint="3–64 letters, digits, dots, dashes or underscores.">
          <input value={form.username} onChange={set("username")} required pattern="[A-Za-z0-9_.\-]{3,64}" autoComplete="username" />
        </Field>
        <Field label="Display name">
          <input value={form.display_name} onChange={set("display_name")} autoComplete="name" />
        </Field>
        <Field label="Password" hint="At least 10 characters.">
          <input type="password" value={form.password} onChange={set("password")} required minLength={10} autoComplete="new-password" />
        </Field>
        <Field label="Confirm password">
          <input type="password" value={form.confirm} onChange={set("confirm")} required minLength={10} autoComplete="new-password" />
        </Field>
        {error && <Notice>{error}</Notice>}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Creating account…" : "Create owner account"}</button>
      </form>
    </AuthFrame>
  );
}

export function LoginPage() {
  const accept = useSession((s) => s.accept);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      accept(await api.auth.login(username, password));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame title="Sign in to NOVA" intro="Your assistant, projects and history stay on this computer.">
      <form onSubmit={submit} className="stack">
        <Field label="Username">
          <input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />
        </Field>
        <Field label="Password">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
        </Field>
        {error && <Notice>{error}</Notice>}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </AuthFrame>
  );
}
