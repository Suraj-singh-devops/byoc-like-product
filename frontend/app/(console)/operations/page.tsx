"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { OperationStatusBadge } from "@/components/Badges";
import { ProgressBar } from "@/components/Meter";
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDuration, formatRelative, humanize } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { Operation, Page } from "@/lib/types";

const TYPES = ["", "CREATE_CLUSTER", "SCALE_CLUSTER", "DELETE_CLUSTER", "HEALTH_CHECK"];
const STATUSES = [
  "",
  "PENDING",
  "VALIDATING",
  "PROVISIONING",
  "BOOTSTRAPPING",
  "CONFIGURING",
  "HEALTH_CHECK",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];
const PAGE_SIZE = 25;

function OperationsList() {
  useTitle("Operations");
  const params = useSearchParams();
  const clusterId = params.get("cluster_id");
  const now = useNow();
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (type) query.set("type", type);
  if (status) query.set("status", status);
  if (clusterId) query.set("cluster_id", clusterId);
  const { data, error, loading } = usePolling<Page<Operation>>(
    (signal) => api(`/operations?${query.toString()}`, { signal }),
    4000,
    [query.toString()],
  );

  return (
    <>
      <PageHeader
        title="Operations"
        subtitle="Every long-running action has an operation you can follow, cancel or retry. A confirmed deletion cannot be cancelled."
      />
      <div className="row">
        <select className="select" style={{ width: 200 }} aria-label="Operation type" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {t ? humanize(t) : "All types"}
            </option>
          ))}
        </select>
        <select className="select" style={{ width: 180 }} aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s ? humanize(s) : "All statuses"}
            </option>
          ))}
        </select>
        {clusterId ? (
          <Link href="/operations" className="button button-small">
            Clear cluster filter
          </Link>
        ) : null}
      </div>
      <ErrorAlert error={error} />
      <Card bodyless>
        {loading && !data ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : !data?.items.length ? (
          <EmptyState icon="activity" title="No operations match" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>Cluster</th>
                  <th>Status</th>
                  <th style={{ width: 160 }}>Progress</th>
                  <th>Started by</th>
                  <th>Created</th>
                  <th className="num">Duration</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((op) => (
                  <tr key={op.id}>
                    <td className="cell-title">
                      <Link href={`/operations/${op.id}`}>{humanize(op.operation_type)}</Link>
                      <div className="small muted mono">{op.id.slice(0, 8)}</div>
                    </td>
                    <td>{op.cluster_id ? <Link href={`/clusters/${op.cluster_id}`}>{op.cluster_name}</Link> : "-"}</td>
                    <td>
                      <OperationStatusBadge status={op.status} />
                    </td>
                    <td>
                      <ProgressBar value={op.progress} label={`${humanize(op.operation_type)} progress`} />
                    </td>
                    <td>{op.created_by ?? "-"}</td>
                    <td className="muted" title={op.created_at}>
                      {formatRelative(op.created_at, now)}
                    </td>
                    <td className="num">{op.started_at ? formatDuration(op.started_at, op.completed_at) : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {data && data.total > PAGE_SIZE ? (
        <div className="spread">
          <span className="small muted">
            {offset + 1}-{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <div className="row">
            <button className="button button-small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <button className="button button-small" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

export default function OperationsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <OperationsList />
    </Suspense>
  );
}
