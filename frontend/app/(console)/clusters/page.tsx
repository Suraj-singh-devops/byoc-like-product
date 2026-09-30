"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { EnvironmentTypeBadge, HealthBadge, LifecycleBadge, PROVIDER_NAMES } from "@/components/Badges";
import { Icon } from "@/components/Icon";
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { engineLabel, formatRelative } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { ClusterSummary, Environment } from "@/lib/types";

export default function ClustersPage() {
  useTitle("Clusters");
  const { can } = useAuth();
  const now = useNow();
  const [query, setQuery] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const [environmentId, setEnvironmentId] = useState("");
  const { data, error, loading } = usePolling<ClusterSummary[]>(
    (signal) =>
      api(`/clusters?include_deleted=${showDeleted}${environmentId ? `&environment_id=${environmentId}` : ""}`, {
        signal,
      }),
    5000,
    [showDeleted, environmentId],
  );
  const environments = usePolling<Environment[]>((signal) => api("/environments", { signal }), 30000);

  const clusters = useMemo(() => {
    const term = query.trim().toLowerCase();
    return (data ?? []).filter((c) => !term || c.name.includes(term) || c.region.includes(term));
  }, [data, query]);

  return (
    <>
      <PageHeader
        title="Clusters"
        subtitle="Every cluster runs in a network you registered in one of your environments."
        actions={
          can("cluster:create") ? (
            <Link href="/clusters/create" className="button button-primary">
              <Icon name="plus" /> Create cluster
            </Link>
          ) : null
        }
      />
      <div className="row">
        <input
          className="input"
          style={{ maxWidth: 320 }}
          placeholder="Filter by name or region"
          aria-label="Filter clusters"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="input"
          style={{ maxWidth: 220 }}
          aria-label="Environment"
          value={environmentId}
          onChange={(e) => setEnvironmentId(e.target.value)}
        >
          <option value="">All environments</option>
          {environments.data?.map((env) => (
            <option key={env.id} value={env.id}>
              {env.name}
            </option>
          ))}
        </select>
        <label className="checkbox small">
          <input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />
          Show deleted clusters
        </label>
      </div>
      <ErrorAlert error={error} />
      <Card bodyless>
        {loading && !data ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : clusters.length === 0 ? (
          <EmptyState icon="database" title={query ? "No matching clusters" : "No clusters yet"}>
            {!query && can("cluster:create") ? (
              <Link href="/clusters/create" className="button button-primary">
                Create a cluster
              </Link>
            ) : null}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Environment</th>
                  <th>Engine</th>
                  <th>Cloud / region</th>
                  <th className="num">Nodes</th>
                  <th>Machine</th>
                  <th className="num">Storage</th>
                  <th>Lifecycle</th>
                  <th>Health</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {clusters.map((c) => (
                  <tr key={c.id}>
                    <td className="cell-title">
                      <Link href={`/clusters/${c.id}`}>{c.name}</Link>
                    </td>
                    <td>
                      {c.environment ? (
                        <span className="row" style={{ gap: 6 }}>
                          {c.environment.name} <EnvironmentTypeBadge type={c.environment.type} />
                        </span>
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                    <td>{engineLabel(c.engine, c.engine_version)}</td>
                    <td>
                      {PROVIDER_NAMES[c.cloud_provider] ?? c.cloud_provider} / {c.region}
                    </td>
                    <td className="num">{c.node_count}</td>
                    <td>{c.machine_type}</td>
                    <td className="num">{c.storage_gb} GB</td>
                    <td>
                      <LifecycleBadge lifecycle={c.lifecycle} />
                    </td>
                    <td>
                      {c.lifecycle === "ACTIVE" || c.lifecycle === "SCALING" ? (
                        <HealthBadge health={c.health} />
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                    <td className="muted" title={c.created_at}>
                      {formatRelative(c.created_at, now)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
