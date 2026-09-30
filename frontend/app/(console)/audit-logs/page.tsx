"use client";

import { Fragment, useState } from "react";

import { AuditStatusBadge } from "@/components/Badges";
import { Icon } from "@/components/Icon";
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatAbsolute, humanize } from "@/lib/format";
import { usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { AuditLog, Page } from "@/lib/types";

// Audit event names (TRD §35, docs/api.md).
const ACTIONS = [
  "",
  "CLUSTER_CREATE_STARTED",
  "CLUSTER_CREATED",
  "CLUSTER_SCALE_STARTED",
  "CLUSTER_SCALE_COMPLETED",
  "CLUSTER_DELETE_STARTED",
  "CLUSTER_DELETE_COMPLETED",
  "CLUSTER_HEALTH_CHECK_REQUESTED",
  "OPERATION_FAILED",
  "OPERATION_CANCELLED",
  "OPERATION_CANCEL_REQUESTED",
  "OPERATION_RETRIED",
  "CLOUD_ACCOUNT_CREATED",
  "CLOUD_ACCOUNT_VALIDATED",
  "CLOUD_ACCOUNT_DELETED",
  "ENVIRONMENT_CREATED",
  "ENVIRONMENT_DELETED",
  "NETWORK_CREATED",
  "NETWORK_VALIDATED",
  "NETWORK_DELETED",
  "MEMBER_ADDED",
  "MEMBER_ROLE_CHANGED",
  "MEMBER_REMOVED",
  "LOGIN_SUCCEEDED",
  "LOGIN_FAILED",
  "USER_SIGNED_UP",
  "ORGANIZATION_SWITCHED",
  "FAULT_INJECTED",
];
const PAGE_SIZE = 50;

export default function AuditLogsPage() {
  useTitle("Audit logs");
  const [action, setAction] = useState("");
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (action) query.set("action", action);
  if (status) query.set("status", status);
  if (user.trim()) query.set("user", user.trim());
  const { data, error, loading } = usePolling<Page<AuditLog>>(
    (signal) => api(`/audit-logs?${query.toString()}`, { signal }),
    10000,
    [query.toString()],
  );

  return (
    <>
      <PageHeader
        title="Audit logs"
        subtitle="Who did what, when, to which resource, and the result. Every change is logged when it starts and when it completes or fails."
      />
      <div className="row">
        <select className="select" style={{ width: 230 }} aria-label="Action" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>
              {a ? humanize(a) : "All actions"}
            </option>
          ))}
        </select>
        <select className="select" style={{ width: 160 }} aria-label="Result" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">All results</option>
          <option value="SUCCESS">Success</option>
          <option value="FAILURE">Failure</option>
        </select>
        <input
          className="input"
          style={{ maxWidth: 260 }}
          placeholder="Filter by user email"
          aria-label="Filter by user"
          value={user}
          onChange={(e) => { setUser(e.target.value); setOffset(0); }}
        />
      </div>
      <ErrorAlert error={error} />
      <Card bodyless>
        {loading && !data ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : !data?.items.length ? (
          <EmptyState icon="list" title="No audit entries match" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>User</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Result</th>
                  <th>IP address</th>
                  <th aria-label="Details" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((entry) => (
                  <Fragment key={entry.id}>
                    <tr>
                      <td className="nums" style={{ whiteSpace: "nowrap" }}>
                        {formatAbsolute(entry.timestamp)}
                      </td>
                      <td>{entry.user ?? "system"}</td>
                      <td>{humanize(entry.action)}</td>
                      <td>
                        {entry.resource ?? "-"}
                        <div className="small muted">{humanize(entry.resource_type)}</div>
                      </td>
                      <td>
                        <AuditStatusBadge status={entry.status} />
                      </td>
                      <td className="mono">{entry.ip_address ?? "-"}</td>
                      <td>
                        <button
                          className="icon-button"
                          aria-expanded={expanded === entry.id}
                          aria-label="Show details"
                          onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                        >
                          <Icon name={expanded === entry.id ? "chevronDown" : "chevronRight"} />
                        </button>
                      </td>
                    </tr>
                    {expanded === entry.id ? (
                      <tr>
                        <td colSpan={7}>
                          <pre className="json">
                            {JSON.stringify(
                              {
                                user: entry.user,
                                organization: entry.organization,
                                action: entry.action,
                                resource: entry.resource,
                                resource_id: entry.resource_id,
                                timestamp: entry.timestamp,
                                status: entry.status,
                                operation_id: entry.operation_id,
                                details: entry.details,
                              },
                              null,
                              2,
                            )}
                          </pre>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
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
