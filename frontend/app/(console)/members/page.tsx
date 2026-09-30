"use client";

import { useState, type FormEvent } from "react";

import { Icon } from "@/components/Icon";
import { Card, Dialog, ErrorAlert, Field, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative, roleLabel } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { Member, Role } from "@/lib/types";

const ROLES: { role: Role; description: string }[] = [
  { role: "OWNER", description: "Full access, including managing Owners." },
  { role: "ADMIN", description: "Everything except managing Owners: clusters (including delete), cloud accounts, members." },
  { role: "OPERATOR", description: "Create, scale and health-check any cluster; cannot delete clusters or manage accounts and members." },
  { role: "VIEWER", description: "Read-only access to everything in the organization." },
];

function AddMemberDialog({ onClose, onAdded, canGrantOwner }: { onClose: () => void; onAdded: () => void; canGrantOwner: boolean }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("OPERATOR");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const fields = error instanceof ApiError ? error.fields : {};

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/organizations/current/members", {
        method: "POST",
        body: { email, name: name || undefined, role, password: password || undefined },
      });
      onAdded();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Add a member"
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-primary" onClick={() => void submit()} disabled={busy || !email}>
            Add member
          </button>
        </>
      }
    >
      <ErrorAlert error={error} />
      <form className="form" onSubmit={submit}>
        <Field label="Email" htmlFor="member-email" error={fields.email}>
          <input id="member-email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <div className="field-row">
          <Field label="Name" htmlFor="member-name">
            <input id="member-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Role" htmlFor="member-role" error={fields.role}>
            <select id="member-role" className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.filter((r) => canGrantOwner || r.role !== "OWNER").map((r) => (
                <option key={r.role} value={r.role}>
                  {roleLabel(r.role)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field
          label="Initial password"
          htmlFor="member-password"
          error={fields.password}
          hint="Needed only if the person has no account yet (at least 8 characters)."
        >
          <input
            id="member-password"
            className="input"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
      </form>
    </Dialog>
  );
}

export default function MembersPage() {
  useTitle("Members");
  const { me, can } = useAuth();
  const now = useNow();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const { data, error: loadError, loading, refresh } = usePolling<Member[]>(
    (signal) => api("/organizations/current/members", { signal }),
    30000,
  );
  const canManage = can("member:manage");
  const isOwner = me?.role === "OWNER";

  const changeRole = async (member: Member, role: Role) => {
    setError(null);
    try {
      await api(`/organizations/current/members/${member.user_id}`, { method: "PATCH", body: { role } });
      await refresh();
    } catch (err) {
      setError(err);
    }
  };

  const remove = async (member: Member) => {
    if (!window.confirm(`Remove ${member.email} from ${me?.organization.name}?`)) return;
    setError(null);
    try {
      await api(`/organizations/current/members/${member.user_id}`, { method: "DELETE" });
      await refresh();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <>
      <PageHeader
        title="Members"
        subtitle={`People in ${me?.organization.name ?? "your organization"} and their roles.`}
        actions={
          canManage ? (
            <button className="button button-primary" onClick={() => setAdding(true)}>
              <Icon name="plus" /> Add member
            </button>
          ) : null
        }
      />
      <ErrorAlert error={error ?? loadError} />
      <Card bodyless>
        {loading && !data ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Joined</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data?.map((member) => {
                  const editable = canManage && (isOwner || member.role !== "OWNER");
                  return (
                    <tr key={member.user_id}>
                      <td className="cell-title">
                        {member.name}
                        {member.user_id === me?.user.id ? <span className="pill" style={{ marginLeft: 6 }}>you</span> : null}
                      </td>
                      <td>{member.email}</td>
                      <td>
                        {editable ? (
                          <select
                            className="select"
                            style={{ width: 150 }}
                            aria-label={`Role of ${member.email}`}
                            value={member.role}
                            onChange={(e) => void changeRole(member, e.target.value as Role)}
                          >
                            {ROLES.filter((r) => isOwner || r.role !== "OWNER").map((r) => (
                              <option key={r.role} value={r.role}>
                                {roleLabel(r.role)}
                              </option>
                            ))}
                          </select>
                        ) : (
                          roleLabel(member.role)
                        )}
                      </td>
                      <td className="muted">{formatRelative(member.joined_at, now)}</td>
                      <td>
                        {editable ? (
                          <button className="icon-button" aria-label={`Remove ${member.email}`} onClick={() => void remove(member)}>
                            <Icon name="trash" />
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Roles">
        <dl className="kv" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
          {ROLES.map((r) => (
            <div key={r.role}>
              <dt>{roleLabel(r.role)}</dt>
              <dd style={{ fontWeight: 400 }}>{r.description}</dd>
            </div>
          ))}
        </dl>
      </Card>
      {adding ? (
        <AddMemberDialog
          canGrantOwner={isOwner}
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            void refresh();
          }}
        />
      ) : null}
    </>
  );
}
