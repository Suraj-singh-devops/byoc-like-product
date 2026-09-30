"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { OperationStatusBadge } from "@/components/Badges";
import { Icon } from "@/components/Icon";
import { ProgressBar } from "@/components/Meter";
import { Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatAbsolute, formatClock, formatDuration, humanize } from "@/lib/format";
import { usePolling } from "@/lib/hooks";
import { canManageOperation } from "@/lib/permissions";
import { useTitle } from "@/lib/title";
import type { Operation, OperationStep } from "@/lib/types";

function StepIcon({ step }: { step: OperationStep }) {
  switch (step.status) {
    case "completed":
      return <Icon name="check" size={13} />;
    case "running":
      return <Icon name="loader" size={13} className="spinner" />;
    case "failed":
      return <Icon name="x" size={13} />;
    case "cancelled":
      return <Icon name="slash" size={13} />;
    default:
      return <Icon name="clock" size={13} />;
  }
}

export default function OperationPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const { data: op, error, loading, refresh } = usePolling<Operation>(
    (signal) => api(`/operations/${id}`, { signal }),
    2000,
    [id],
  );
  useTitle(op ? humanize(op.operation_type) : "Operation");
  const logRef = useRef<HTMLPreElement>(null);
  const logLength = op?.log.length ?? 0;
  useEffect(() => {
    const element = logRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [logLength]);

  if (loading && !op) return <Spinner />;
  if (!op) return <ErrorAlert error={error ?? "Operation not found."} />;

  const act = async (kind: "cancel" | "retry") => {
    setBusy(true);
    setActionError(null);
    try {
      const result = await api<Operation>(`/operations/${op.id}/${kind}`, { method: "POST" });
      if (kind === "retry") router.push(`/operations/${result.id}`);
      else await refresh();
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(false);
    }
  };

  const allowed = canManageOperation(can, op.operation_type);
  const retryable = allowed && (op.status === "FAILED" || op.status === "CANCELLED");
  const params = Object.entries(op.params).filter(([key]) => key !== "spec" && key !== "previous_lifecycle");

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <Link href="/operations">Operations</Link>
            <Icon name="chevronRight" size={12} />
            <span className="mono">{op.id.slice(0, 8)}</span>
          </>
        }
        title={
          <span className="row" style={{ gap: 10 }}>
            {humanize(op.operation_type)}
            <OperationStatusBadge status={op.status} />
          </span>
        }
        subtitle={
          <>
            {op.cluster_id ? (
              <>
                Cluster <Link href={`/clusters/${op.cluster_id}`}>{op.cluster_name}</Link> ·{" "}
              </>
            ) : null}
            started by {op.created_by ?? "system"} · {formatAbsolute(op.created_at)}
            {op.attempt > 1 ? ` · attempt ${op.attempt}` : ""}
            {op.retry_of ? (
              <>
                {" "}
                · retry of <Link href={`/operations/${op.retry_of}`}>{op.retry_of.slice(0, 8)}</Link>
              </>
            ) : null}
          </>
        }
        actions={
          <>
            {!op.is_terminal && allowed && op.operation_type !== "DELETE_CLUSTER" ? (
              <button className="button" onClick={() => void act("cancel")} disabled={busy || !op.cancellable}>
                <Icon name="slash" /> {op.cancel_requested ? "Stopping at the next safe point..." : "Cancel"}
              </button>
            ) : null}
            {retryable ? (
              <button className="button button-primary" onClick={() => void act("retry")} disabled={busy}>
                <Icon name="refresh" /> Retry
              </button>
            ) : null}
          </>
        }
      />
      <ErrorAlert error={actionError} />

      {op.error ? (
        <div className="alert alert-error" role="alert">
          <Icon name="alertOctagon" />
          <div>
            <div className="alert-title">{op.error.message}</div>
            {op.error.reason ? <div className="alert-detail">Reason: {op.error.reason}</div> : null}
            {op.error.suggested_action ? <div className="alert-detail">Suggested action: {op.error.suggested_action}</div> : null}
            <div className="alert-detail small">Error code: {op.error.code}</div>
          </div>
        </div>
      ) : null}

      <Card>
        <div className="stack" style={{ gap: 8 }}>
          <div className="spread small">
            <span>
              {op.is_terminal ? humanize(op.status) : `${humanize(op.status)}${op.current_step ? ` · ${humanize(op.current_step)}` : ""}`}
            </span>
            <span className="muted nums">
              {op.progress}% · {op.started_at ? formatDuration(op.started_at, op.completed_at) : "waiting for a worker"}
            </span>
          </div>
          <ProgressBar value={op.progress} label="Operation progress" />
        </div>
      </Card>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Card title="Steps">
          {op.steps.length ? (
            <ol className="steps">
              {op.steps.map((step) => (
                <li key={step.key} className="step" data-status={step.status}>
                  <span className="step-icon">
                    <StepIcon step={step} />
                  </span>
                  <div>
                    <div className="step-name">{step.name}</div>
                    {step.message ? <div className="step-message">{step.message}</div> : null}
                  </div>
                  <span className="step-time">
                    {step.started_at ? formatDuration(step.started_at, step.completed_at) : ""}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted">Waiting for a worker to pick up the operation.</p>
          )}
        </Card>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Log" description="Most recent last">
            {op.log.length ? (
              <pre className="log" ref={logRef}>
                {op.log.map((entry, index) => (
                  <div key={index}>
                    <time>{formatClock(entry.at)}</time> {entry.message}
                  </div>
                ))}
              </pre>
            ) : (
              <p className="muted">No log entries yet.</p>
            )}
          </Card>
          {params.length || op.result ? (
            <Card title="Details">
              <pre className="json">{JSON.stringify({ params: Object.fromEntries(params), result: op.result }, null, 2)}</pre>
            </Card>
          ) : null}
        </div>
      </div>
    </>
  );
}
