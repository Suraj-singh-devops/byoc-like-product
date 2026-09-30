import Link from "next/link";

import { engineLabel, formatRelative, humanize } from "@/lib/format";
import type { ClusterSummary } from "@/lib/types";

import { ClusterStatusBadge, PROVIDER_NAMES } from "./Badges";
import { Meter, ProgressBar } from "./Meter";

export function ClusterCard({ cluster, now }: { cluster: ClusterSummary; now: number }) {
  const metrics = cluster.metrics ?? {};
  const active = cluster.lifecycle === "ACTIVE" || cluster.lifecycle === "SCALING" || cluster.lifecycle === "UPDATING";
  const op = cluster.active_operation;
  return (
    <article className="card cluster-card">
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div>
          <Link href={`/clusters/${cluster.id}`} className="name">
            {cluster.name}
          </Link>
          <div className="facts" style={{ marginTop: 4 }}>
            <span>{engineLabel(cluster.engine, cluster.engine_version)}</span>
            <span>
              {PROVIDER_NAMES[cluster.cloud_provider] ?? cluster.cloud_provider} / {cluster.region}
            </span>
            {cluster.environment ? <span>{cluster.environment.name}</span> : null}
          </div>
        </div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <ClusterStatusBadge lifecycle={cluster.lifecycle} health={cluster.health} />
        </div>
      </div>

      <div className="facts">
        <span>
          {cluster.node_count} node{cluster.node_count === 1 ? "" : "s"}
        </span>
        <span>{cluster.storage_gb} GB each</span>
        <span>{cluster.machine_type}</span>
        {cluster.high_availability ? <span>HA</span> : null}
      </div>

      {op ? (
        <div className="stack" style={{ gap: 6 }}>
          <div className="spread small">
            <span>
              {humanize(op.operation_type)}: {humanize(op.status)}
            </span>
            <span className="muted nums">{op.progress}%</span>
          </div>
          <ProgressBar value={op.progress} label={`${humanize(op.operation_type)} progress`} />
        </div>
      ) : null}

      {active ? (
        <div className="meters">
          <Meter label="CPU" value={metrics.cpu_percent} />
          <Meter label="Memory" value={metrics.memory_percent} />
          <Meter label="Disk" value={metrics.disk_percent} />
          <Meter label="JVM heap" value={metrics.engine?.jvm_heap_percent} thresholds={{ warning: 85, critical: 92 }} />
        </div>
      ) : cluster.lifecycle === "FAILED" && cluster.status_message ? (
        <p className="small muted">{cluster.status_message}</p>
      ) : null}

      <div className="spread">
        <span className="small muted">
          {active && metrics.updated_at ? `Metrics ${formatRelative(metrics.updated_at, now)}` : humanize(cluster.lifecycle)}
        </span>
        <Link href={`/clusters/${cluster.id}`} className="button button-small">
          Open
        </Link>
      </div>
    </article>
  );
}
