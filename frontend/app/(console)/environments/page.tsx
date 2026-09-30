"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { EnvironmentTypeBadge, ProviderBadge } from "@/components/Badges";
import { EnvironmentDialog } from "@/components/EnvironmentDialog";
import { Icon } from "@/components/Icon";
import { Card, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { Environment } from "@/lib/types";

export default function EnvironmentsPage() {
  useTitle("Environments");
  const { can } = useAuth();
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const { data, error, loading } = usePolling<Environment[]>((signal) => api("/environments", { signal }), 15000);
  const canManage = can("environment:manage");

  return (
    <>
      <PageHeader
        title="Environments"
        subtitle="Group networks and clusters, for example test and production. Each network points at a VPC you already run on GCP or AWS."
        actions={
          canManage ? (
            <button className="button button-primary" onClick={() => setCreating(true)}>
              <Icon name="plus" /> Create environment
            </button>
          ) : null
        }
      />
      <ErrorAlert error={error} />
      {loading && !data ? <Spinner /> : null}
      {data && data.length === 0 ? (
        <Card>
          <EmptyState icon="layers" title="No environments yet">
            <p>
              Start with an environment (test or production), register the network your clusters should use, then
              create clusters in it.
            </p>
            {canManage ? (
              <button className="button button-primary" onClick={() => setCreating(true)}>
                Create environment
              </button>
            ) : (
              <p className="muted">Ask an Owner or Admin to create one.</p>
            )}
          </EmptyState>
        </Card>
      ) : null}
      <div className="grid grid-auto">
        {data?.map((env) => (
          <article key={env.id} className="card cluster-card">
            <div className="spread" style={{ alignItems: "flex-start" }}>
              <div>
                <Link href={`/environments/${env.id}`} className="name">
                  {env.name}
                </Link>
                {env.description ? <p className="small muted">{env.description}</p> : null}
              </div>
              <EnvironmentTypeBadge type={env.type} />
            </div>
            <div className="facts">
              <span>
                {env.network_count} network{env.network_count === 1 ? "" : "s"}
              </span>
              <span>
                {env.cluster_count} cluster{env.cluster_count === 1 ? "" : "s"}
              </span>
            </div>
            <div className="spread">
              <span className="row" style={{ gap: 6 }}>
                {env.providers.length ? (
                  env.providers.map((p) => <ProviderBadge key={p} provider={p} />)
                ) : (
                  <span className="small muted">No network yet</span>
                )}
              </span>
              <Link href={`/environments/${env.id}`} className="button button-small">
                Open
              </Link>
            </div>
          </article>
        ))}
      </div>
      {creating ? (
        <EnvironmentDialog
          onClose={() => setCreating(false)}
          onCreated={(env) => router.push(`/environments/${env.id}`)}
        />
      ) : null}
    </>
  );
}
