"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import {
  AgentStatusBadge,
  EnvironmentTypeBadge,
  HealthBadge,
  HealthIcon,
  LifecycleBadge,
  OperationStatusBadge,
  SeverityIcon,
  PROVIDER_NAMES,
} from "@/components/Badges";
import { DeleteDialog, FaultDialog, ScaleDialog } from "@/components/ClusterDialogs";
import { Icon } from "@/components/Icon";
import { ProgressBar } from "@/components/Meter";
import { MetricsPanel } from "@/components/MetricsPanel";
import { Alert, Card, Copyable, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { engineLabel, formatBytes, formatDuration, formatPercent, formatRelative, humanize, withKeyOrder } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { canManageOperation } from "@/lib/permissions";
import { useTitle } from "@/lib/title";
import type { ClusterDetail, ClusterEvent, ClusterHealth, NodeHealth, NodeInfo, Operation, Page } from "@/lib/types";

type DialogKind = "scale" | "delete" | "fault" | null;

function componentLabel(health: NodeHealth | ClusterHealth | null | undefined): string {
  return health ? humanize(health).toLowerCase() : "unknown";
}

function NodesTable({ nodes, now }: { nodes: NodeInfo[]; now: number }) {
  if (!nodes.length) return <p className="card-body muted">Nodes appear once the VMs are created.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Node</th>
            <th>Role</th>
            <th>Zone</th>
            <th>Private IP</th>
            <th>Lifecycle</th>
            <th>VM</th>
            <th>Agent</th>
            <th>Health</th>
            <th className="num">CPU</th>
            <th className="num">JVM heap</th>
            <th className="num">Disk</th>
            <th>Last report</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => {
            // A stopped VM's or a silent agent's last numbers are history, not the current state.
            const live = node.instance_status === "RUNNING" && node.agent_status === "REPORTING";
            const note = node.health === "HEALTHY" ? node.health_warnings[0] : node.health_reasons[0];
            return (
              <tr key={node.id}>
                <td className="cell-title">
                  {node.name}
                  {node.engine_metrics.is_master ? <span className="pill" style={{ marginLeft: 6 }}>elected master</span> : null}
                  <div className="small muted">{node.instance_name}</div>
                </td>
                <td>{node.role.split(",").join(", ")}</td>
                <td style={{ whiteSpace: "nowrap" }}>{node.zone}</td>
                <td className="mono" style={{ whiteSpace: "nowrap" }}>{node.private_ip ?? "-"}</td>
                <td>
                  {humanize(node.lifecycle)}
                  {node.lifecycle === "BOOTSTRAPPING" && node.bootstrap_status ? (
                    <div className="small muted">{humanize(node.bootstrap_status)}</div>
                  ) : null}
                </td>
                <td style={{ whiteSpace: "nowrap" }}>{humanize(node.instance_status ?? "unknown")}</td>
                <td>
                  <AgentStatusBadge status={node.agent_status} />
                </td>
                <td>
                  <span
                    className="row"
                    style={{ gap: 6, flexWrap: "nowrap", whiteSpace: "nowrap" }}
                    title={[...node.health_reasons, ...node.health_warnings].join("\n") || undefined}
                  >
                    <HealthIcon health={node.health} />
                    {humanize(node.health)}
                  </span>
                  {node.health !== "HEALTHY" ? (
                    <div className="small muted" style={{ whiteSpace: "nowrap" }}>
                      VM {componentLabel(node.infrastructure_health)} · Elasticsearch {componentLabel(node.engine_health)}
                    </div>
                  ) : null}
                  {note ? (
                    <div className="small muted" style={{ maxWidth: 260 }}>
                      {note}
                    </div>
                  ) : null}
                </td>
                <td className="num">{live ? formatPercent(node.system.cpu_percent) : "-"}</td>
                <td className="num">{live ? formatPercent(node.engine_metrics.jvm_heap_percent) : "-"}</td>
                <td className="num">{live ? formatPercent(node.system.disk_percent) : "-"}</td>
                <td className="muted" title={node.last_report_at ?? undefined}>
                  {formatRelative(node.last_report_at, now)}
                  {node.report_source ? (
                    <div className="small">{node.report_source === "heartbeat" ? "agent heartbeat" : "guest attributes"}</div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ClusterPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { meta, can } = useAuth();
  const now = useNow(5000);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const cluster = usePolling<ClusterDetail>((signal) => api(`/clusters/${id}`, { signal }), 3000, [id]);
  const operations = usePolling<Page<Operation>>(
    (signal) => api(`/operations?cluster_id=${id}&limit=10`, { signal }),
    4000,
    [id],
  );
  const events = usePolling<ClusterEvent[]>((signal) => api(`/clusters/${id}/events?limit=15`, { signal }), 5000, [id]);
  const c = cluster.data;
  useTitle(c?.name);

  if (cluster.loading && !c) return <Spinner />;
  if (!c) return <ErrorAlert error={cluster.error ?? "Cluster not found."} />;

  const op = c.active_operation;
  const deleted = c.lifecycle === "DELETED";
  const serving = c.lifecycle === "ACTIVE" || c.lifecycle === "SCALING";
  const infra = c.actual_state.infrastructure ?? {};
  const actualNodes = c.actual_state.nodes?.count;
  const drift = c.generation !== c.observed_generation;
  const installed = c.actual_state.engine?.version;
  const failedOp = operations.data?.items.find((o) => o.status === "FAILED" || o.status === "CANCELLED");
  const canRetry = failedOp ? canManageOperation(can, failedOp.operation_type) : false;
  const actions = {
    fault: Boolean(meta?.mock_mode && c.simulated) && can("cluster:operate") && c.lifecycle === "ACTIVE",
    healthCheck: can("cluster:health_check") && !deleted,
    scale: can("cluster:scale") && !deleted,
    delete: can("cluster:delete") && !deleted,
  };

  const startHealthCheck = async () => {
    setActionError(null);
    try {
      await api(`/clusters/${c.id}/health-check`, { method: "POST" });
      setNotice("Health check started. The result appears under Operations.");
      void operations.refresh();
    } catch (err) {
      setActionError(err);
    }
  };

  const retry = async (operationId: string) => {
    setActionError(null);
    try {
      const retried = await api<Operation>(`/operations/${operationId}/retry`, { method: "POST" });
      router.push(`/operations/${retried.id}`);
    } catch (err) {
      setActionError(err);
    }
  };

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <Link href="/clusters">Clusters</Link>
            <Icon name="chevronRight" size={12} />
            <span>{c.name}</span>
          </>
        }
        title={
          <span className="row" style={{ gap: 10 }}>
            {c.name}
            <LifecycleBadge lifecycle={c.lifecycle} />
            {serving ? <HealthBadge health={c.health} /> : null}
          </span>
        }
        subtitle={`${engineLabel(c.engine, c.engine_version)} · ${PROVIDER_NAMES[c.cloud_provider] ?? c.cloud_provider} ${c.region} · created ${formatRelative(c.created_at, now)}${c.created_by ? ` by ${c.created_by}` : ""}`}
        actions={
          <>
            {actions.fault ? (
              <button className="button" onClick={() => setDialog("fault")}>
                <Icon name="flask" /> Simulate failure
              </button>
            ) : null}
            {actions.healthCheck ? (
              <button className="button" onClick={startHealthCheck} disabled={!serving}>
                <Icon name="heart" /> Health check
              </button>
            ) : null}
            {actions.scale ? (
              <button
                className="button"
                onClick={() => setDialog("scale")}
                disabled={c.lifecycle !== "ACTIVE" || Boolean(op)}
                title="Add nodes (scaling down is not available)"
              >
                <Icon name="scale" /> Scale up
              </button>
            ) : null}
            {actions.delete ? (
              <button className="button" onClick={() => setDialog("delete")} disabled={c.lifecycle === "DELETING"}>
                <Icon name="trash" /> Delete
              </button>
            ) : null}
          </>
        }
      />

      {notice ? (
        <Alert tone="success" title={notice} />
      ) : null}
      <ErrorAlert error={actionError} />

      {op ? (
        <Card>
          <div className="stack" style={{ gap: 8 }}>
            <div className="spread">
              <div className="row">
                <OperationStatusBadge status={op.status} />
                <strong>{humanize(op.operation_type)}</strong>
                <span className="muted">{op.current_step ? `· ${humanize(op.current_step)}` : ""}</span>
              </div>
              <Link href={`/operations/${op.id}`} className="button button-small">
                View progress
              </Link>
            </div>
            <ProgressBar value={op.progress} label="Operation progress" />
          </div>
        </Card>
      ) : null}

      {c.status_message ? (
        <div className={`alert ${c.lifecycle === "FAILED" ? "alert-error" : "alert-warning"}`} role="alert">
          <Icon name={c.lifecycle === "FAILED" ? "alertOctagon" : "alertTriangle"} />
          <div style={{ flex: 1 }}>
            <div className="alert-title">{c.status_message}</div>
            {failedOp && canRetry && !deleted ? (
              <div className="row" style={{ marginTop: 8 }}>
                <Link className="button button-small" href={`/operations/${failedOp.id}`}>
                  Details
                </Link>
                <button className="button button-small" onClick={() => retry(failedOp.id)}>
                  <Icon name="refresh" size={14} /> Retry
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="grid split-2-1">
        <Card title="Overview">
          <dl className="kv">
            <div>
              <dt>Engine</dt>
              <dd>
                {engineLabel(c.engine, c.engine_version)}
                {installed && !Array.isArray(installed) ? <span className="muted"> (running {installed})</span> : null}
              </dd>
            </div>
            <div>
              <dt>Environment</dt>
              <dd>
                {c.environment ? (
                  <Link href={`/environments/${c.environment.id}`} className="row" style={{ gap: 6 }}>
                    {c.environment.name} <EnvironmentTypeBadge type={c.environment.type} />
                  </Link>
                ) : (
                  "-"
                )}
              </dd>
            </div>
            <div>
              <dt>Cloud</dt>
              <dd>
                {PROVIDER_NAMES[c.cloud_provider] ?? c.cloud_provider}
                {c.simulated ? " (simulated)" : ""} · {c.cloud_account_name ?? "-"}
              </dd>
            </div>
            <div>
              <dt>{c.cloud_provider === "aws" ? "AWS account" : "Project"}</dt>
              <dd>{c.project_id}</dd>
            </div>
            <div className="kv-wide">
              <dt>Network</dt>
              <dd className="break">
                {c.network ? (
                  <>
                    {c.network.name} · {c.network.vpc.split("/").pop()} ·{" "}
                    {c.network.subnets.map((s) => `${s.id.split("/").pop()} ${s.cidr}`).join(", ")}
                  </>
                ) : (
                  "Dedicated VPC (created before registered networks)"
                )}
              </dd>
            </div>
            <div>
              <dt>Region</dt>
              <dd>{c.region}</dd>
            </div>
            <div>
              <dt>Zones</dt>
              <dd>{Array.from(new Set(c.nodes.map((n) => n.zone))).join(", ") || c.zone}</dd>
            </div>
            <div>
              <dt>Nodes</dt>
              <dd>
                {actualNodes ?? c.nodes.length}
                {actualNodes !== undefined && actualNodes !== c.node_count ? (
                  <span className="muted"> (desired {c.node_count})</span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt>Machine</dt>
              <dd>{c.machine_type}</dd>
            </div>
            <div>
              <dt>Storage</dt>
              <dd>
                {c.storage_gb} GB {c.storage_type} per node
              </dd>
            </div>
            <div>
              <dt>High availability</dt>
              <dd>{c.high_availability ? "Enabled (multi-zone)" : "Disabled"}</dd>
            </div>
            <div>
              <dt>Disk used</dt>
              <dd>
                {formatBytes(c.metrics.disk_used_bytes)} of {formatBytes(c.metrics.disk_total_bytes)}
              </dd>
            </div>
          </dl>
        </Card>

        <Card
          title="Health"
          description={c.last_health_check_at ? `Checked ${formatRelative(c.last_health_check_at, now)}` : "Not checked yet"}
        >
          <div className="stack">
            <div className="row">
              <HealthBadge health={c.health} />
              {c.health_details.engine_status ? (
                <span className="pill">cluster status: {c.health_details.engine_status}</span>
              ) : null}
            </div>
            <dl className="kv">
              <div>
                <dt>Infrastructure</dt>
                <dd className="row" style={{ gap: 6 }}>
                  <HealthIcon health={c.health_details.infrastructure ?? "UNKNOWN"} size={14} />
                  {humanize(c.health_details.infrastructure ?? "UNKNOWN")}
                  <span className="muted small">VMs and system metrics</span>
                </dd>
              </div>
              <div>
                <dt>Elasticsearch</dt>
                <dd className="row" style={{ gap: 6 }}>
                  <HealthIcon health={c.health_details.engine ?? "UNKNOWN"} size={14} />
                  {humanize(c.health_details.engine ?? "UNKNOWN")}
                  <span className="muted small">as reported by the agents</span>
                </dd>
              </div>
            </dl>
            <div className="small muted">
              {c.health_details.nodes_reporting ?? 0} of {c.health_details.nodes_expected ?? c.node_count} nodes reporting
              {c.health_details.engine_node_count !== undefined && c.health_details.engine_node_count !== null
                ? ` · ${c.health_details.engine_node_count} joined the cluster`
                : ""}
            </div>
            {c.health_details.reasons?.length ? (
              <ul className="stack" style={{ paddingLeft: 18, margin: 0, gap: 4 }}>
                {c.health_details.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : c.health === "HEALTHY" ? (
              <p className="muted">All checks pass.</p>
            ) : null}
            {c.health_details.warnings?.length ? (
              <div className="stack" style={{ gap: 4 }}>
                <div className="small muted">Warnings (they do not change health)</div>
                <ul className="stack small" style={{ paddingLeft: 18, margin: 0, gap: 2 }}>
                  {c.health_details.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </Card>
      </div>

      {!deleted ? (
        <Card
          title="Connect"
          description="Private endpoints: reachable from the network's subnets (other ranges need firewall rules you add)."
        >
          <div className="stack">
            {infra.http_endpoints?.length ? (
              <div className="row" style={{ gap: 12 }}>
                {infra.http_endpoints.map((endpoint) => (
                  <Copyable key={endpoint} value={endpoint} />
                ))}
              </div>
            ) : (
              <p className="muted">Endpoints appear once the VMs are provisioned.</p>
            )}
            {infra.secrets?.elastic_password ? (
              <p className="small muted">
                User <code>elastic</code>. The password never leaves your{" "}
                {c.cloud_provider === "aws" ? "account; read it from Secrets Manager:" : "project; read it from Secret Manager:"}{" "}
                <Copyable
                  value={
                    c.cloud_provider === "aws"
                      ? `aws secretsmanager get-secret-value --secret-id ${infra.secrets.elastic_password} --region ${c.region}`
                      : `gcloud secrets versions access latest --secret=${infra.secrets.elastic_password} --project=${c.project_id}`
                  }
                />
                {c.simulated ? " (simulated)" : ""}
              </p>
            ) : null}
          </div>
        </Card>
      ) : null}

      {serving ? <MetricsPanel clusterId={c.id} /> : null}

      <Card title="Nodes" description={`${c.nodes.length} node${c.nodes.length === 1 ? "" : "s"}`} bodyless>
        <NodesTable nodes={c.nodes} now={now} />
      </Card>

      <div className="grid grid-2">
        <Card title="Operations" actions={<Link className="small" href={`/operations?cluster_id=${c.id}`}>All</Link>} bodyless>
          {operations.data?.items.length ? (
            <ul className="list">
              {operations.data.items.map((o) => (
                <li key={o.id} className="list-item" style={{ alignItems: "center" }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Link href={`/operations/${o.id}`}>{humanize(o.operation_type)}</Link>
                    <div className="meta">
                      {formatRelative(o.created_at, now)} · {formatDuration(o.started_at ?? o.created_at, o.completed_at)}
                      {o.created_by ? ` · ${o.created_by}` : ""}
                    </div>
                  </div>
                  <OperationStatusBadge status={o.status} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="card-body muted">No operations.</p>
          )}
        </Card>
        <Card title="Events" description="Failure detection and lifecycle" bodyless>
          {events.data?.length ? (
            <ul className="list">
              {events.data.map((event) => (
                <li key={event.id} className="list-item">
                  <SeverityIcon severity={event.severity} />
                  <div style={{ minWidth: 0 }}>
                    <div>{event.message}</div>
                    <div className="meta">{formatRelative(event.created_at, now)}</div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="card-body muted">No events.</p>
          )}
        </Card>
      </div>

      <Card
        title="Desired vs actual state"
        description={
          drift
            ? `Reconciling: desired generation ${c.generation}, observed ${c.observed_generation}`
            : `In sync at generation ${c.generation}`
        }
      >
        <div className="diff-grid">
          <div className="stack" style={{ gap: 6 }}>
            <h3>Desired</h3>
            <pre className="json">
              {JSON.stringify(
                withKeyOrder(
                  {
                    ...c.desired_state,
                    cloud: withKeyOrder((c.desired_state.cloud as Record<string, unknown>) ?? {}, [
                      "provider",
                      "accountId",
                      "project",
                      "region",
                      "zone",
                    ]),
                  },
                  ["cluster", "engine", "cloud", "compute", "storage", "nodes", "highAvailability", "generation"],
                ),
                null,
                2,
              )}
            </pre>
          </div>
          <div className="stack" style={{ gap: 6 }}>
            <h3>Actual</h3>
            <pre className="json">
              {JSON.stringify(
                withKeyOrder(c.actual_state, [
                  "engine",
                  "nodes",
                  "health",
                  "observedGeneration",
                  "observedAt",
                  "infrastructure",
                  "engine_settings",
                ]),
                null,
                2,
              )}
            </pre>
          </div>
        </div>
      </Card>

      {dialog === "scale" ? (
        <ScaleDialog
          cluster={c}
          onClose={() => setDialog(null)}
          onDone={(operationId) => router.push(`/operations/${operationId}`)}
        />
      ) : null}
      {dialog === "delete" ? (
        <DeleteDialog
          cluster={c}
          onClose={() => setDialog(null)}
          onDone={(operationId) => router.push(`/operations/${operationId}`)}
        />
      ) : null}
      {dialog === "fault" && meta?.mock_mode ? (
        <FaultDialog
          cluster={c}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            setNotice("Fault injected. The monitor detects it within about 15 seconds.");
          }}
        />
      ) : null}
    </>
  );
}
