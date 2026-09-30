"use client";

import { useEffect, useState } from "react";

import { ProviderBadge } from "@/components/Badges";
import { CloudAccountDialog } from "@/components/CloudAccountDialog";
import { Icon, type IconName } from "@/components/Icon";
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { CloudAccount, CloudAccountStatus } from "@/lib/types";

const STATUS: Record<CloudAccountStatus, { tone: string; icon: IconName; label: string }> = {
  CONNECTED: { tone: "good", icon: "checkCircle", label: "Connected" },
  VALIDATING: { tone: "accent", icon: "loader", label: "Validating" },
  PENDING: { tone: "neutral", icon: "clock", label: "Not validated" },
  FAILED: { tone: "critical", icon: "alertOctagon", label: "Failed" },
  DISCONNECTED: { tone: "serious", icon: "slash", label: "Disconnected" },
};

function StatusBadge({ account }: { account: CloudAccount }) {
  const spec = STATUS[account.status] ?? STATUS.PENDING;
  return (
    <span className={`badge badge-${spec.tone}`}>
      <Icon name={spec.icon} size={14} className={account.status === "VALIDATING" ? "spinner" : undefined} /> {spec.label}
    </span>
  );
}

function AccountCard({
  account,
  canManage,
  onChanged,
  now,
}: {
  account: CloudAccount;
  canManage: boolean;
  onChanged: () => void;
  now: number;
}) {
  const [busy, setBusy] = useState<"validate" | "delete" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const validation = account.validation;

  const act = async (kind: "validate" | "delete") => {
    if (kind === "delete" && !window.confirm(`Remove the cloud account ${account.name}? No cloud resources are touched.`)) return;
    setBusy(kind);
    setError(null);
    try {
      await api(`/cloud-accounts/${account.id}${kind === "validate" ? "/validate" : ""}`, {
        method: kind === "validate" ? "POST" : "DELETE",
      });
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2 className="row" style={{ gap: 10 }}>
            {account.name} <ProviderBadge provider={account.provider} /> <StatusBadge account={account} />
          </h2>
          <p>
            {account.provider === "aws" ? "AWS account" : "Project"} <strong>{account.project_id}</strong>
            {account.region ? ` · ${account.region}` : ""} · {account.cluster_count} cluster
            {account.cluster_count === 1 ? "" : "s"} · {account.network_count} network
            {account.network_count === 1 ? "" : "s"}
          </p>
        </div>
        {canManage ? (
          <div className="row">
            <button className="button button-small" onClick={() => void act("validate")} disabled={busy !== null}>
              <Icon name="refresh" size={14} /> {busy === "validate" ? "Validating..." : "Validate"}
            </button>
            <button className="button button-small" onClick={() => void act("delete")} disabled={busy !== null || account.cluster_count > 0 || account.network_count > 0}>
              <Icon name="trash" size={14} /> Remove
            </button>
          </div>
        ) : null}
      </div>
      <div className="card-body stack">
        <ErrorAlert error={error} />
        <dl className="kv">
          <div>
            <dt>Access</dt>
            <dd>
              {account.auth_type === "local_credentials"
                ? "Your own credentials (development only)"
                : account.provider === "aws"
                  ? "Assumed role (keyless)"
                  : "Impersonation (keyless)"}
            </dd>
          </div>
          <div className="kv-wide">
            <dt>{account.auth_type === "local_credentials" ? "Acts as" : account.provider === "aws" ? "Role" : "Service account"}</dt>
            <dd className="break">
              {account.auth_type === "local_credentials"
                ? "The gcloud application default credentials of the machine running the platform (no service account)"
                : (account.role_arn ?? account.service_account_email ?? "-")}
            </dd>
          </div>
          <div>
            <dt>Last validated</dt>
            <dd>{formatRelative(account.last_validated_at, now)}</dd>
          </div>
          <div>
            <dt>Last connected</dt>
            <dd>{formatRelative(account.last_connected_at, now)}</dd>
          </div>
        </dl>
        {validation ? (
          <ul className="check-list">
            {validation.checks.map((check) => (
              <li key={check.key} data-status={check.status}>
                <Icon name={check.status === "passed" ? "checkCircle" : check.status === "failed" ? "alertOctagon" : "alertTriangle"} />
                <span>
                  <strong>{check.name}</strong>
                  {check.status === "passed" ? <span className="muted"> · {check.message}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {validation?.error ? (
          <div className="alert alert-error">
            <Icon name="alertOctagon" />
            <div>
              <div className="alert-title">{validation.error.message}</div>
              {validation.error.reason ? <div className="alert-detail">Reason: {validation.error.reason}</div> : null}
              {validation.error.suggested_action ? (
                <div className="alert-detail">Suggested action: {validation.error.suggested_action}</div>
              ) : null}
              {validation.missing_permissions.length ? (
                <div className="alert-detail">
                  Missing permissions:{" "}
                  {validation.missing_permissions.map((p) => (
                    <code key={p} style={{ marginRight: 6 }}>
                      {p}
                    </code>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default function CloudAccountsPage() {
  useTitle("Cloud accounts");
  const { can, meta } = useAuth();
  const now = useNow();
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  // Validation runs in the terraform-runner: refresh quickly until it settles.
  const { data, error, loading, refresh } = usePolling<CloudAccount[]>(
    (signal) => api("/cloud-accounts", { signal }),
    busy ? 2000 : 15000,
  );
  useEffect(() => {
    setBusy(Boolean(data?.some((a) => a.status === "VALIDATING" || a.status === "PENDING")));
  }, [data]);
  const canManage = can("cloud_account:manage");

  return (
    <>
      <PageHeader
        title="Cloud accounts"
        subtitle="GCP projects and AWS accounts the platform may provision databases into. Infrastructure is billed to you."
        actions={
          canManage ? (
            <button className="button button-primary" onClick={() => setAdding(true)}>
              <Icon name="plus" /> Add cloud account
            </button>
          ) : null
        }
      />
      <ErrorAlert error={error} />
      {loading && !data ? <Spinner /> : null}
      {data && data.length === 0 ? (
        <Card>
          <EmptyState icon="cloud" title="No cloud accounts yet">
            <p>
              Give the platform keyless access: a service account it may impersonate in a GCP project, or a role it may
              assume in an AWS account with your organization&apos;s external ID. Then add it here.
            </p>
            {canManage ? (
              <button className="button button-primary" onClick={() => setAdding(true)}>
                Add cloud account
              </button>
            ) : null}
          </EmptyState>
        </Card>
      ) : null}
      <div className="stack" style={{ gap: 16 }}>
        {data?.map((account) => (
          <AccountCard key={account.id} account={account} canManage={canManage} onChanged={() => void refresh()} now={now} />
        ))}
      </div>
      {adding ? (
        <CloudAccountDialog
          mock={Boolean(meta?.mock_mode)}
          onClose={() => setAdding(false)}
          onCreated={() => {
            setAdding(false);
            void refresh();
          }}
        />
      ) : null}
    </>
  );
}
