"use client";

import Link from "next/link";

import { HealthIcon, OperationStatusBadge, SeverityIcon } from "@/components/Badges";
import { ClusterCard } from "@/components/ClusterCard";
import { Icon } from "@/components/Icon";
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative, humanize } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { CloudAccount, ClusterEvent, ClusterHealth, ClusterSummary, Operation, Page } from "@/lib/types";

interface DashboardData {
  clusters: ClusterSummary[];
  operations: Operation[];
  events: ClusterEvent[];
  accounts: CloudAccount[];
}

function StatTile({ label, value, health, sub }: { label: string; value: number; health?: ClusterHealth; sub?: string }) {
  return (
    <div className="card stat">
      <div className="stat-label">
        {health ? <HealthIcon health={health} size={14} /> : null}
        {label}
      </div>
      <div className="stat-value">{value}</div>
      {sub ? <div className="stat-sub">{sub}</div> : null}
    </div>
  );
}

export default function DashboardPage() {
  useTitle("Dashboard");
  const { me, can } = useAuth();
  const now = useNow();
  const { data, error, loading } = usePolling<DashboardData>(
    async (signal) => {
      const [clusters, operations, events, accounts] = await Promise.all([
        api<ClusterSummary[]>("/clusters", { signal }),
        api<Page<Operation>>("/operations?limit=6", { signal }),
        api<ClusterEvent[]>("/events?limit=8", { signal }),
        api<CloudAccount[]>("/cloud-accounts", { signal }),
      ]);
      return { clusters, operations: operations.items, events, accounts };
    },
    5000,
  );

  const clusters = data?.clusters ?? [];
  // Health counts cover clusters that exist and are serving; CREATING, FAILED and DELETING are lifecycle states.
  const serving = clusters.filter((c) => c.lifecycle === "ACTIVE" || c.lifecycle === "SCALING" || c.lifecycle === "UPDATING");
  const count = (health: ClusterHealth) => serving.filter((c) => c.health === health).length;
  const busy = clusters.filter((c) => c.active_operation).length;
  const canCreate = can("cluster:create");

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={`${me?.organization.name ?? ""} · database clusters running in your cloud accounts`}
        actions={
          canCreate ? (
            <Link href="/clusters/create" className="button button-primary">
              <Icon name="plus" /> Create cluster
            </Link>
          ) : null
        }
      />
      <ErrorAlert error={error} />
      {loading && !data ? <Spinner /> : null}

      {data ? (
        <div className="stack" style={{ gap: 24 }}>
          <div className="grid grid-4">
            <StatTile label="Clusters" value={clusters.length} sub={busy ? `${busy} with an operation in progress` : "All idle"} />
            <StatTile label="Healthy" value={count("HEALTHY")} health="HEALTHY" />
            <StatTile label="Degraded" value={count("DEGRADED")} health="DEGRADED" />
            <StatTile
              label="Unhealthy"
              value={count("UNHEALTHY")}
              health="UNHEALTHY"
              sub={count("UNKNOWN") ? `${count("UNKNOWN")} unknown` : undefined}
            />
          </div>

          {clusters.length === 0 ? (
            <Card>
              <EmptyState icon="database" title="No clusters yet">
                {data.accounts.length === 0 ? (
                  <>
                    <p>
                      Start by connecting the GCP project or AWS account where your databases should run, then create an
                      environment and register its network.
                    </p>
                    <Link href="/cloud-accounts" className="button button-primary">
                      Add a cloud account
                    </Link>
                  </>
                ) : canCreate ? (
                  <>
                    <p>Pick an environment and a registered network, then create an Elasticsearch cluster in it.</p>
                    <Link href="/clusters/create" className="button button-primary">
                      Create your first cluster
                    </Link>
                  </>
                ) : (
                  <p>Clusters created by your team will appear here.</p>
                )}
              </EmptyState>
            </Card>
          ) : (
            <section className="stack">
              <div className="spread">
                <h2>Clusters</h2>
                <Link href="/clusters" className="small">
                  View all
                </Link>
              </div>
              <div className="grid grid-auto">
                {clusters.map((cluster) => (
                  <ClusterCard key={cluster.id} cluster={cluster} now={now} />
                ))}
              </div>
            </section>
          )}

          <div className="grid grid-2">
            <Card title="Recent operations" actions={<Link href="/operations" className="small">All operations</Link>} bodyless>
              {data.operations.length ? (
                <ul className="list">
                  {data.operations.map((op) => (
                    <li key={op.id} className="list-item" style={{ alignItems: "center" }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <Link href={`/operations/${op.id}`}>{humanize(op.operation_type)}</Link>
                        <div className="meta">
                          {op.cluster_name ?? "-"} · {formatRelative(op.created_at, now)}
                          {op.created_by ? ` · ${op.created_by}` : ""}
                        </div>
                      </div>
                      <OperationStatusBadge status={op.status} />
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="card-body muted">No operations yet.</div>
              )}
            </Card>
            <Card title="Recent events" description="Failure detection and lifecycle changes" bodyless>
              {data.events.length ? (
                <ul className="list">
                  {data.events.map((event) => (
                    <li key={event.id} className="list-item">
                      <SeverityIcon severity={event.severity} />
                      <div style={{ minWidth: 0 }}>
                        <div>{event.message}</div>
                        <div className="meta">
                          <Link href={`/clusters/${event.cluster_id}`}>{event.cluster_name ?? "cluster"}</Link> ·{" "}
                          {formatRelative(event.created_at, now)}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="card-body muted">No events yet.</div>
              )}
            </Card>
          </div>
        </div>
      ) : null}
    </>
  );
}
