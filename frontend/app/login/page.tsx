"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { Icon } from "@/components/Icon";
import { ErrorAlert, Field } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { roleLabel } from "@/lib/format";

function LoginForm() {
  const { me, meta, reload } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");
  const destination = next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";

  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [organization, setOrganization] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "Sign in · BYOC Console";
  }, []);

  useEffect(() => {
    if (me) router.replace(destination);
  }, [me, destination, router]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        await api("/auth/login", { method: "POST", body: { email, password } });
      } else {
        await api("/auth/signup", {
          method: "POST",
          body: { email, password, name, organization_name: organization },
        });
      }
      await reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <div className="card-body stack" style={{ gap: 18 }}>
          <div className="row" style={{ gap: 10 }}>
            <span className="brand-mark">
              <Icon name="database" size={16} />
            </span>
            <div>
              <h1 style={{ fontSize: 18 }}>{mode === "login" ? "Sign in" : "Create an organization"}</h1>
              <p className="muted small">Managed databases, running in your own cloud account</p>
            </div>
          </div>

          <ErrorAlert error={error} />

          <form className="form" onSubmit={submit}>
            {mode === "signup" ? (
              <>
                <Field label="Your name" htmlFor="name">
                  <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" />
                </Field>
                <Field label="Organization name" htmlFor="organization">
                  <input
                    id="organization"
                    className="input"
                    value={organization}
                    onChange={(e) => setOrganization(e.target.value)}
                    required
                    autoComplete="organization"
                  />
                </Field>
              </>
            ) : null}
            <Field label="Email" htmlFor="email">
              <input
                id="email"
                className="input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="username"
              />
            </Field>
            <Field label="Password" htmlFor="password" hint={mode === "signup" ? "At least 8 characters." : undefined}>
              <input
                id="password"
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </Field>
            <button className="button button-primary" type="submit" disabled={busy}>
              {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create organization"}
            </button>
          </form>

          {meta?.signup_enabled ? (
            <p className="small muted" style={{ textAlign: "center" }}>
              {mode === "login" ? "New here? " : "Already have an account? "}
              <button
                type="button"
                className="button button-ghost button-small"
                onClick={() => {
                  setMode(mode === "login" ? "signup" : "login");
                  setError(null);
                }}
              >
                {mode === "login" ? "Create an organization" : "Sign in"}
              </button>
            </p>
          ) : null}

          {mode === "login" && meta?.mock_mode && meta.demo_accounts.length ? (
            <div className="stack" style={{ gap: 8 }}>
              <div className="small muted">
                Demo accounts (mock mode). The password is the <code>DEMO_PASSWORD</code> value, by default{" "}
                <code>demo-password</code>.
              </div>
              <div className="demo-accounts">
                {meta.demo_accounts.map((account) => (
                  <button
                    key={account.email}
                    type="button"
                    className="demo-account"
                    onClick={() => setEmail(account.email)}
                  >
                    <span>{account.email}</span>
                    <span className="pill">
                      {roleLabel(account.role)} · {account.organization === "acme" ? "Acme" : "Globex"}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
