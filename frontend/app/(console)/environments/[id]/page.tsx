"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Fragment, useEffect, useState } from "react";

import { EnvironmentTypeBadge, NetworkStatusBadge, ProviderBadge } from "@/components/Badges";
import { ClusterCard } from "@/components/ClusterCard";
import { ProviderPicker } from "@/components/CloudAccountDialog";
import { Icon } from "@/components/Icon";
import { NetworkDetailsView, NetworkDialog } from "@/components/NetworkDialog";
import { Alert, Card, Dialog, EmptyState, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";
import { useNow, usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type { CloudAccount, CloudProviderName, ClusterSummary, Environment, Network } from "@/lib/types";

function NetworkRow({
  network,
  canManage,
  onChanged,
  now,
}: {
  network: Network;
  canManage: boolean;
  onChanged: () => void;
  now: number;
}) {
  const [open, setOpen] = useState(network.status !== "AVAILABLE");
  const [busy, setBusy] = useState<"validate" | "delete" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const warnings = network.details?.warnings.length ?? 0;

  const act = async (kind: "validate" | "delete") => {
    if (kind === "delete" && !window.confirm(`Remove network ${network.name} from the platform? The VPC itself is not touched.`))
      return;
    setBusy(kind);
    setError(null);
    try {
      await api(`/networks/${network.id}${kind === "validate" ? "/validate" : ""}`, {
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
    <Fragment>
      <tr>
        <td className="cell-title">
          <button className="link-button" onClick={() => setOpen(!open)} aria-expanded={open}>
            <Icon name={open ? "chevronDown" : "chevronRight"} size={14} /> {network.name}
          </button>
        </td>
        <td>
          <ProviderBadge provider={network.provider} />
        </td>
        <td className="break">
          {network.vpc}
          <div className="small muted">{network.subnets.join(", ")}</div>
        </td>
        <td>
          {network.region}
          <div className="small muted">{network.zones.join(", ") || "-"}</div>
        </td>
        <td>
          <NetworkStatusBadge status={network.status} />
          {warnings ? (
            <div className="small muted">
              {warnings} warning{warnings === 1 ? "" : "s"}
            </div>
          ) : null}
        </td>
        <td className="num">{network.cluster_count}</td>
        <td>
          {canManage ? (
            <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
              <button className="button button-small" onClick={() => void act("validate")} disabled={busy !== null}>
                <Icon name="refresh" size={14} /> {busy === "validate" ? "Checking..." : "Validate"}
              </button>
              <button
                className="button button-small"
                onClick={() => void act("delete")}
                disabled={busy !== null || network.cluster_count > 0}
                title={network.cluster_count > 0 ? "Delete its clusters first" : undefined}
              >
                <Icon name="trash" size={14} /> Remove
              </button>
            </span>
          ) : null}
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={7}>
            <div className="stack" style={{ gap: 8, padding: "4px 0 8px" }}>
              <ErrorAlert error={error} />
              <span className="small muted">
                {network.cloud_account_name ?? "Cloud account"} · checked {formatRelative(network.last_validated_at, now)}
              </span>
              {network.details ? <NetworkDetailsView details={network.details} /> : <Spinner label="Not checked yet" />}
            </div>
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

export default function EnvironmentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can, meta } = useAuth();
  const now = useNow();
  const [registering, setRegistering] = useState<CloudProviderName | "choose" | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const environment = usePolling<Environment>((signal) => api(`/environments/${id}`, { signal }), 15000, [id]);
  const [networksBusy, setNetworksBusy] = useState(false);
  // Network lookups run in the monitoring-worker: refresh quickly until they settle.
  const networks = usePolling<Network[]>(
    (signal) => api(`/environments/${id}/networks`, { signal }),
    networksBusy ? 2000 : 10000,
    [id],
  );
  useEffect(() => {
    setNetworksBusy(Boolean(networks.data?.some((n) => n.status === "VALIDATING" || n.status === "PENDING")));
  }, [networks.data]);
  const clusters = usePolling<ClusterSummary[]>(
    (signal) => api(`/clusters?environment_id=${id}`, { signal }),
    5000,
    [id],
  );
  const accounts = usePolling<CloudAccount[]>((signal) => api("/cloud-accounts", { signal }), 30000);
  const env = environment.data;
  useTitle(env ? `${env.name} · Environments` : "Environment");
  const canManageNetworks = can("network:manage");
  const refresh = () => {
    void networks.refresh();
    void environment.refresh();
  };

  const remove = async () => {
    if (!env || !window.confirm(`Delete environment ${env.name}?`)) return;
    setDeleteError(null);
    try {
      await api(`/environments/${env.id}`, { method: "DELETE" });
      router.push("/environments");
    } catch (err) {
      setDeleteError(err);
    }
  };

  if (environment.error && !env) return <ErrorAlert error={environment.error} />;
  if (!env) return <Spinner />;
  const empty = env.network_count === 0 && env.cluster_count === 0;

  return (
    <>
      <PageHeader
        breadcrumb={<Link href="/environments">Environments</Link>}
        title={
          <span className="row" style={{ gap: 10 }}>
            {env.name} <EnvironmentTypeBadge type={env.type} />
          </span>
        }
        subtitle={env.description ?? "Networks and clusters of this environment."}
        actions={
          <>
            {can("cluster:create") ? (
              <Link
                href={`/clusters/create?environment=${env.id}`}
                className="button button-primary"
                aria-disabled={env.network_count === 0 || undefined}
              >
                <Icon name="plus" /> Create cluster
              </Link>
            ) : null}
            {can("environment:manage") ? (
              <button
                className="button"
                onClick={() => void remove()}
                disabled={!empty}
                title={empty ? undefined : "Delete its clusters and remove its networks first"}
              >
                <Icon name="trash" /> Delete
              </button>
            ) : null}
          </>
        }
      />
      <ErrorAlert error={deleteError} />

      <Card
        title="Networks"
        description="Existing VPCs and subnets in your cloud accounts. Clusters of this environment run in them."
        actions={
          canManageNetworks ? (
            <button className="button button-small button-primary" onClick={() => setRegistering("choose")}>
              <Icon name="plus" size={14} /> Register network
            </button>
          ) : null
        }
        bodyless
      >
        <ErrorAlert error={networks.error} />
        {networks.loading && !networks.data ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : networks.data?.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Cloud</th>
                  <th>VPC / subnets</th>
                  <th>Region / zones</th>
                  <th>Status</th>
                  <th className="num">Clusters</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {networks.data.map((n) => (
                  <NetworkRow key={n.id} network={n} canManage={canManageNetworks} onChanged={refresh} now={now} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon="network" title="No networks yet">
            <p>
              Register the VPC and subnet your clusters should run in. The platform looks their details up in your
              cloud account; it does not create them.
            </p>
            {canManageNetworks ? (
              <button className="button button-primary" onClick={() => setRegistering("choose")}>
                Register network
              </button>
            ) : (
              <p className="muted">Ask an Owner or Admin to register one.</p>
            )}
          </EmptyState>
        )}
      </Card>

      <Card title="Clusters" bodyless>
        {clusters.data?.length ? (
          <div className="card-body">
            <div className="grid grid-auto">
              {clusters.data.map((c) => (
                <ClusterCard key={c.id} cluster={c} now={now} />
              ))}
            </div>
          </div>
        ) : (
          <EmptyState icon="database" title="No clusters in this environment">
            {env.network_count === 0 ? <p className="muted">Register a network first.</p> : null}
          </EmptyState>
        )}
      </Card>

      {registering === "choose" ? (
        <Dialog title="Which cloud is the network in?" onClose={() => setRegistering(null)}>
          <div className="stack">
            <ProviderPicker value={null} onChange={(p) => setRegistering(p)} />
            <Alert tone="info" title="One environment, any cloud">
              An environment can hold networks on GCP and AWS. Add the cloud account under Cloud accounts first.
            </Alert>
          </div>
        </Dialog>
      ) : registering ? (
        <NetworkDialog
          environmentId={env.id}
          environmentName={env.name}
          provider={registering}
          accounts={accounts.data ?? []}
          mock={Boolean(meta?.mock_mode)}
          onClose={() => setRegistering(null)}
          onCreated={() => {
            setRegistering(null);
            refresh();
          }}
        />
      ) : null}
    </>
  );
}
