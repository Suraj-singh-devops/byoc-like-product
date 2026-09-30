"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from "react";

import { EnvironmentTypeBadge, NetworkStatusBadge, PROVIDER_NAMES } from "@/components/Badges";
import { ProviderPicker } from "@/components/CloudAccountDialog";
import { EnvironmentDialog } from "@/components/EnvironmentDialog";
import { Icon } from "@/components/Icon";
import { NetworkDialog } from "@/components/NetworkDialog";
import { Alert, Card, EmptyState, ErrorAlert, Field, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { usePolling } from "@/lib/hooks";
import { useTitle } from "@/lib/title";
import type {
  CloudAccount,
  CloudCatalog,
  CloudProviderName,
  ClusterLayout,
  EngineCatalog,
  Environment,
  MachineType,
  Network,
  NodeGroup,
  NodeGroupName,
  OperationAccepted,
} from "@/lib/types";

type Step = "environment" | "provider" | "network" | "configure";
const STEPS: { key: Step; label: string }[] = [
  { key: "environment", label: "Environment" },
  { key: "provider", label: "Cloud provider" },
  { key: "network", label: "Network" },
  { key: "configure", label: "Configure" },
];

interface SpecState {
  name: string;
  engine: string;
  version: string;
  zone: string;
  machine_type: string;
  node_count: number;
  storage_gb: number;
  storage_type: string;
  high_availability: boolean;
  layout: ClusterLayout;
  node_groups: Record<NodeGroupName, NodeGroup>;
}

const GROUP_ORDER: NodeGroupName[] = ["master", "data", "coordinating"];

function Steps({ current, onJump }: { current: Step; onJump: (step: Step) => void }) {
  const index = STEPS.findIndex((s) => s.key === current);
  return (
    <ol className="wizard-steps" aria-label="Steps">
      {STEPS.map((step, i) => {
        const state = i < index ? "done" : i === index ? "current" : "todo";
        return (
          <li key={step.key} data-state={state} aria-current={state === "current" ? "step" : undefined}>
            {state === "done" ? (
              <button type="button" className="link-button" onClick={() => onJump(step.key)}>
                <Icon name="checkCircle" size={14} /> {step.label}
              </button>
            ) : (
              <>
                <span className="nums">{i + 1}.</span> {step.label}
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export default function CreateClusterPage() {
  useTitle("Create cluster");
  const router = useRouter();
  const { can, meta } = useAuth();
  const idempotencyKey = useRef(newIdempotencyKey());
  const mock = Boolean(meta?.mock_mode);

  const setup = usePolling(
    async (signal) => {
      const [engines, environments, accounts] = await Promise.all([
        api<EngineCatalog[]>("/engines", { signal }),
        api<Environment[]>("/environments", { signal }),
        api<CloudAccount[]>("/cloud-accounts", { signal }),
      ]);
      return { engines, environments, accounts };
    },
    null,
  );
  const engine = setup.data?.engines[0];

  const [step, setStep] = useState<Step>("environment");
  const [environment, setEnvironment] = useState<Environment | null>(null);
  const [provider, setProvider] = useState<CloudProviderName | null>(null);
  const [network, setNetwork] = useState<Network | null>(null);
  const [dialog, setDialog] = useState<"environment" | "network" | null>(null);
  const [networks, setNetworks] = useState<Network[] | null>(null);

  // Arriving from an environment page (?environment=<id>) skips the first step.
  useEffect(() => {
    if (environment || !setup.data) return;
    const wanted = new URLSearchParams(window.location.search).get("environment");
    const found = setup.data.environments.find((e) => e.id === wanted);
    if (found) {
      setEnvironment(found);
      setStep("provider");
    }
  }, [setup.data, environment]);

  const loadNetworks = async (env: Environment) => {
    setNetworks(null);
    setNetworks(await api<Network[]>(`/environments/${env.id}/networks`));
  };
  useEffect(() => {
    if (environment) void loadNetworks(environment).catch(() => setNetworks([]));
  }, [environment]);

  if (!can("cluster:create")) {
    return (
      <>
        <PageHeader title="Create cluster" />
        <Alert tone="warning" title="You do not have permission to create clusters.">
          Ask an Owner, Admin or Operator of your organization.
        </Alert>
      </>
    );
  }

  const providerNetworks = (networks ?? []).filter((n) => n.provider === provider);

  return (
    <>
      <PageHeader
        title="Create database"
        subtitle="Choose where the cluster runs: an environment, a cloud, and a network you registered. You pay your cloud provider directly for the infrastructure."
        breadcrumb={
          <>
            <Link href="/clusters">Clusters</Link>
            <Icon name="chevronRight" size={12} />
            <span>Create</span>
          </>
        }
      />
      <Steps current={step} onJump={setStep} />
      {setup.loading && !setup.data ? <Spinner /> : null}
      <ErrorAlert error={setup.error} />

      {setup.data && step === "environment" ? (
        <Card
          title="1. Which environment?"
          description="Environments group networks and clusters, for example test and production."
          actions={
            can("environment:manage") ? (
              <button className="button button-small" onClick={() => setDialog("environment")}>
                <Icon name="plus" size={14} /> New environment
              </button>
            ) : null
          }
        >
          {setup.data.environments.length ? (
            <div className="choice-grid" role="radiogroup" aria-label="Environment">
              {setup.data.environments.map((env) => (
                <button
                  key={env.id}
                  type="button"
                  role="radio"
                  aria-checked={environment?.id === env.id}
                  className="choice"
                  data-selected={environment?.id === env.id || undefined}
                  onClick={() => {
                    setEnvironment(env);
                    setNetwork(null);
                    setStep("provider");
                  }}
                >
                  <Icon name="layers" size={18} />
                  <span>
                    <strong className="row" style={{ gap: 6 }}>
                      {env.name} <EnvironmentTypeBadge type={env.type} />
                    </strong>
                    <span className="muted small">
                      {env.network_count} network{env.network_count === 1 ? "" : "s"} · {env.cluster_count} cluster
                      {env.cluster_count === 1 ? "" : "s"}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState icon="layers" title="No environments yet">
              {can("environment:manage") ? (
                <button className="button button-primary" onClick={() => setDialog("environment")}>
                  Create your first environment
                </button>
              ) : (
                <p className="muted">Ask an Owner or Admin to create an environment.</p>
              )}
            </EmptyState>
          )}
        </Card>
      ) : null}

      {environment && step === "provider" ? (
        <Card
          title="2. Which cloud provider?"
          description={`The cloud of the network the cluster will run in (environment ${environment.name}).`}
        >
          <div className="stack">
            <ProviderPicker
              value={provider}
              onChange={(p) => {
                setProvider(p);
                setNetwork(null);
                setStep("network");
              }}
            />
            {networks ? (
              <span className="small muted">
                Registered in {environment.name}:{" "}
                {(["gcp", "aws"] as const)
                  .map((p) => `${networks.filter((n) => n.provider === p).length} ${PROVIDER_NAMES[p]}`)
                  .join(" · ")}
              </span>
            ) : null}
          </div>
        </Card>
      ) : null}

      {environment && provider && step === "network" ? (
        <Card
          title="3. Which network?"
          description={`An existing ${PROVIDER_NAMES[provider]} VPC and subnet registered in ${environment.name}. Its details were fetched from your cloud account.`}
          actions={
            can("network:manage") ? (
              <button className="button button-small" onClick={() => setDialog("network")}>
                <Icon name="plus" size={14} /> Register network
              </button>
            ) : null
          }
        >
          {networks === null ? (
            <Spinner />
          ) : providerNetworks.length ? (
            <div className="choice-grid" role="radiogroup" aria-label="Network">
              {providerNetworks.map((n) => {
                const usable = n.status === "AVAILABLE";
                return (
                  <button
                    key={n.id}
                    type="button"
                    role="radio"
                    aria-checked={network?.id === n.id}
                    className="choice"
                    data-selected={network?.id === n.id || undefined}
                    disabled={!usable}
                    onClick={() => {
                      setNetwork(n);
                      setStep("configure");
                    }}
                  >
                    <Icon name="network" size={18} />
                    <span>
                      <strong className="row" style={{ gap: 6 }}>
                        {n.name} <NetworkStatusBadge status={n.status} />
                      </strong>
                      <span className="muted small break">
                        {n.region} · {n.vpc} · {n.zones.length} zone{n.zones.length === 1 ? "" : "s"}
                      </span>
                      {n.details?.warnings.length ? (
                        <span className="small">
                          {n.details.warnings.length} warning{n.details.warnings.length === 1 ? "" : "s"}
                        </span>
                      ) : null}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <EmptyState icon="network" title={`No ${PROVIDER_NAMES[provider]} network in ${environment.name}`}>
              {can("network:manage") ? (
                <button className="button button-primary" onClick={() => setDialog("network")}>
                  Register a network
                </button>
              ) : (
                <p className="muted">Ask an Owner or Admin to register one.</p>
              )}
            </EmptyState>
          )}
        </Card>
      ) : null}

      {environment && network && engine && step === "configure" ? (
        <ConfigureStep
          engine={engine}
          engines={setup.data?.engines ?? []}
          environment={environment}
          network={network}
          idempotencyKey={idempotencyKey}
          onCreated={(result) => router.push(`/clusters/${result.cluster_id}`)}
        />
      ) : null}

      {dialog === "environment" ? (
        <EnvironmentDialog
          onClose={() => setDialog(null)}
          onCreated={(env) => {
            setDialog(null);
            setEnvironment(env);
            setNetwork(null);
            setStep("provider");
            void setup.refresh();
          }}
        />
      ) : null}
      {dialog === "network" && environment && provider ? (
        <NetworkDialog
          environmentId={environment.id}
          environmentName={environment.name}
          provider={provider}
          accounts={setup.data?.accounts ?? []}
          mock={mock}
          onClose={() => setDialog(null)}
          onCreated={(created) => {
            setDialog(null);
            void (async () => {
              // The lookup runs in the monitoring-worker; wait for it to settle, then continue.
              let current = created;
              for (let i = 0; i < 60 && (current.status === "VALIDATING" || current.status === "PENDING"); i++) {
                await new Promise((resolve) => setTimeout(resolve, 1000));
                current = await api<Network>(`/networks/${created.id}`);
              }
              await loadNetworks(environment);
              if (current.status === "AVAILABLE") {
                setNetwork(current);
                setStep("configure");
              }
            })();
          }}
        />
      ) : null}
    </>
  );
}

function ConfigureStep({
  engine,
  engines,
  environment,
  network,
  idempotencyKey,
  onCreated,
}: {
  engine: EngineCatalog;
  engines: EngineCatalog[];
  environment: Environment;
  network: Network;
  idempotencyKey: RefObject<string>;
  onCreated: (result: OperationAccepted) => void;
}) {
  const production = environment.type === "PRODUCTION";
  const haPossible = network.zones.length >= 3;
  const [catalog, setCatalog] = useState<CloudCatalog | null>(null);
  const [machines, setMachines] = useState<MachineType[]>([]);
  const [form, setForm] = useState<SpecState>({
    name: "",
    engine: engine.engine,
    version: engine.default_version,
    zone: network.zones[0] ?? "",
    machine_type: "",
    node_count: production && haPossible ? 3 : 1,
    storage_gb: 100,
    storage_type: "",
    high_availability: production && haPossible,
    // Production gets dedicated master, data and coordinating nodes (docs/adr/0016).
    layout: production && haPossible && engine.node_groups.length ? "dedicated" : "combined",
    node_groups: Object.fromEntries(
      engine.node_groups.map((g) => [
        g.name,
        { name: g.name, count: g.default_count, machine_type: "", storage_gb: g.default_storage_gb },
      ]),
    ) as Record<NodeGroupName, NodeGroup>,
  });
  const dedicated = form.layout === "dedicated";
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  // Machine and storage catalogs of the network's cloud.
  useEffect(() => {
    let cancelled = false;
    api<CloudCatalog>(`/cloud-accounts/${network.cloud_account_id}/catalog`)
      .then((result) => {
        if (cancelled) return;
        setCatalog(result);
        setForm((f) => ({
          ...f,
          machine_type: f.machine_type || result.default_machine_type,
          storage_type: f.storage_type || result.storage_types[0]?.name || "",
          node_groups: Object.fromEntries(
            Object.entries(f.node_groups).map(([name, g]) => [
              name,
              { ...g, machine_type: g.machine_type || result.default_machine_type },
            ]),
          ) as Record<NodeGroupName, NodeGroup>,
        }));
      })
      .catch(setError);
    return () => {
      cancelled = true;
    };
  }, [network.cloud_account_id]);

  useEffect(() => {
    if (!form.zone) return;
    let cancelled = false;
    api<MachineType[]>(`/cloud-accounts/${network.cloud_account_id}/machine-types?zone=${encodeURIComponent(form.zone)}`)
      .then((result) => !cancelled && setMachines(result))
      .catch(setError);
    return () => {
      cancelled = true;
    };
  }, [network.cloud_account_id, form.zone]);

  const machine = machines.find((m) => m.name === form.machine_type);
  const fieldErrors = error instanceof ApiError ? error.fields : {};
  const clientErrors = useMemo(() => {
    const errors: Record<string, string> = {};
    if (form.name && !/^[a-z][a-z0-9-]{1,38}[a-z0-9]$/.test(form.name)) {
      errors.name = "3-40 lowercase letters, digits and hyphens; start with a letter.";
    }
    if (!dedicated && form.high_availability && form.node_count < engine.ha_min_nodes) {
      errors.node_count = `High availability needs at least ${engine.ha_min_nodes} nodes.`;
    }
    if (dedicated) {
      for (const def of engine.node_groups) {
        const group = form.node_groups[def.name];
        if (!group) continue;
        const min = form.high_availability ? Math.max(def.min, def.ha_min) : def.min;
        if (group.count < min || group.count > def.max) {
          errors[`node_groups.${def.name}.count`] =
            def.min === def.max
              ? `Exactly ${def.min}.`
              : `${min}-${def.max}${form.high_availability && def.ha_min > def.min ? " with high availability" : ""}.`;
        }
        if (group.storage_gb < engine.min_storage_gb) {
          errors[`node_groups.${def.name}.storage_gb`] = `At least ${engine.min_storage_gb} GB.`;
        }
        const m = machines.find((x) => x.name === group.machine_type);
        if (m && m.memory_gb < engine.min_memory_gb) {
          errors[`node_groups.${def.name}.machine_type`] = `At least ${engine.min_memory_gb} GB of memory.`;
        }
      }
      const archs = new Set(
        Object.values(form.node_groups)
          .map((g) => machines.find((m) => m.name === g.machine_type)?.architecture)
          .filter(Boolean),
      );
      if (archs.size > 1) errors["node_groups.data.machine_type"] = "All groups must use the same CPU architecture.";
    }
    if (form.high_availability && !haPossible) {
      errors.high_availability = `This network has ${network.zones.length} zone(s); high availability needs 3.`;
    }
    if (form.storage_gb < engine.min_storage_gb) errors.storage_gb = `At least ${engine.min_storage_gb} GB.`;
    if (machine && machine.memory_gb < engine.min_memory_gb) {
      errors.machine_type = `${engine.display_name} needs at least ${engine.min_memory_gb} GB of memory.`;
    }
    return errors;
  }, [form, engine, machine, machines, dedicated, haPossible, network.zones.length]);
  const errorFor = (field: string) => clientErrors[field] ?? fieldErrors[field];
  const update = <K extends keyof SpecState>(key: K, value: SpecState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const updateGroup = (name: NodeGroupName, patch: Partial<NodeGroup>) =>
    setForm((f) => ({ ...f, node_groups: { ...f.node_groups, [name]: { ...f.node_groups[name], ...patch } } }));
  const groups = GROUP_ORDER.map((name) => form.node_groups[name]).filter(Boolean);
  const totalNodes = dedicated ? groups.reduce((sum, g) => sum + g.count, 0) : form.node_count;
  const totalStorage = dedicated
    ? groups.reduce((sum, g) => sum + g.count * g.storage_gb, 0)
    : form.node_count * form.storage_gb;
  const haZones = form.high_availability
    ? [form.zone, ...network.zones.filter((z) => z !== form.zone).sort()].slice(0, 3)
    : [form.zone];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (Object.keys(clientErrors).length) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api<OperationAccepted>("/clusters", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey.current },
        body: dedicated
          ? {
              name: form.name,
              engine: form.engine,
              version: form.version,
              zone: form.zone,
              storage_type: form.storage_type,
              high_availability: form.high_availability,
              layout: "dedicated",
              node_groups: Object.fromEntries(
                groups.map((g) => [g.name, { count: g.count, machine_type: g.machine_type, storage_gb: g.storage_gb }]),
              ),
              environment_id: environment.id,
              network_id: network.id,
            }
          : {
              name: form.name,
              engine: form.engine,
              version: form.version,
              zone: form.zone,
              machine_type: form.machine_type,
              node_count: form.node_count,
              storage_gb: form.storage_gb,
              storage_type: form.storage_type,
              high_availability: form.high_availability,
              layout: "combined",
              environment_id: environment.id,
              network_id: network.id,
            },
      });
      onCreated(result);
    } catch (err) {
      // A definitive rejection means nothing was created: the next attempt is a new request.
      if (err instanceof ApiError && err.status > 0) idempotencyKey.current = newIdempotencyKey();
      setError(err);
      setSubmitting(false);
    }
  };

  return (
    <form className="create-layout" onSubmit={submit} noValidate>
      <div className="stack" style={{ gap: 16 }}>
        {error && !Object.keys(fieldErrors).length ? <ErrorAlert error={error} /> : null}
        {error && Object.keys(fieldErrors).length ? <ErrorAlert error={error} title="Some fields need attention" /> : null}
        <Card title="4. Database">
          <div className="form">
            <Field
              label="Cluster name"
              htmlFor="name"
              error={errorFor("name")}
              hint="Lowercase letters, digits and hyphens, e.g. production-search."
            >
              <input
                id="name"
                className="input"
                value={form.name}
                onChange={(e) => update("name", e.target.value.toLowerCase())}
                placeholder={`${environment.name}-search`}
                required
                aria-invalid={Boolean(errorFor("name"))}
                autoFocus
              />
            </Field>
            <div className="field-row">
              <Field label="Database engine" htmlFor="engine" hint="Redis, MySQL, PostgreSQL and MongoDB are planned.">
                <select id="engine" className="select" value={form.engine} onChange={(e) => update("engine", e.target.value)}>
                  {engines.map((e) => (
                    <option key={e.engine} value={e.engine}>
                      {e.display_name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Version" htmlFor="version" error={errorFor("version")} hint="Exact versions from the version catalog.">
                <select id="version" className="select" value={form.version} onChange={(e) => update("version", e.target.value)}>
                  {engine.versions
                    .filter((v) => v.status === "supported")
                    .map((v) => (
                      <option key={v.version} value={v.version}>
                        {v.label}
                        {v.default ? " (default)" : ""}
                      </option>
                    ))}
                </select>
              </Field>
            </div>
          </div>
        </Card>

        <Card title="Topology">
          <div className="choice-grid" role="radiogroup" aria-label="Topology">
            <button
              type="button"
              role="radio"
              aria-checked={dedicated}
              className="choice"
              data-selected={dedicated || undefined}
              disabled={!engine.node_groups.length}
              onClick={() => update("layout", "dedicated")}
            >
              <Icon name="layers" size={18} />
              <span>
                <strong>Dedicated roles{production ? " (recommended)" : ""}</strong>
                <span className="muted small">
                  3 master nodes, data nodes and coordinating nodes behind one internal load balancer. A busy data node
                  never slows down master elections.
                </span>
              </span>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={!dedicated}
              className="choice"
              data-selected={!dedicated || undefined}
              onClick={() => update("layout", "combined")}
            >
              <Icon name="server" size={18} />
              <span>
                <strong>Combined</strong>
                <span className="muted small">Every node is master-eligible, holds data and takes requests. Fewer VMs.</span>
              </span>
            </button>
          </div>
        </Card>

        <Card title="Resources">
          <div className="form">
            <div className="field-row">
              <Field
                label={form.high_availability ? "Primary zone" : "Zone"}
                htmlFor="zone"
                error={errorFor("zone")}
                hint={network.provider === "aws" ? "Zones of the network's subnets." : "Zones of the subnet's region."}
              >
                <select id="zone" className="select" value={form.zone} onChange={(e) => update("zone", e.target.value)}>
                  {network.zones.map((z) => (
                    <option key={z} value={z}>
                      {z}
                    </option>
                  ))}
                </select>
              </Field>
              {!dedicated ? (
                <Field label="Machine type" htmlFor="machine" error={errorFor("machine_type")}>
                  <select
                    id="machine"
                    className="select"
                    value={form.machine_type}
                    onChange={(e) => update("machine_type", e.target.value)}
                  >
                    {form.machine_type && !machines.some((m) => m.name === form.machine_type) ? (
                      <option value={form.machine_type}>{form.machine_type}</option>
                    ) : null}
                    {machines.map((m) => (
                      <option key={m.name} value={m.name}>
                        {m.name} · {m.vcpus} vCPU · {m.memory_gb} GB{m.architecture === "arm64" ? " · Arm" : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}
            </div>
            {dedicated ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Group</th>
                      <th>Nodes</th>
                      <th>Machine type</th>
                      <th>Disk (GB)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {engine.node_groups.map((def) => {
                      const group = form.node_groups[def.name];
                      if (!group) return null;
                      const err = (f: string) => errorFor(`node_groups.${def.name}.${f}`);
                      return (
                        <tr key={def.name}>
                          <td>
                            <strong>{def.label}</strong>
                            <div className="small muted">{def.description}</div>
                          </td>
                          <td>
                            <input
                              className="input"
                              type="number"
                              aria-label={`${def.label}: nodes`}
                              min={def.min}
                              max={def.max}
                              value={group.count}
                              disabled={def.min === def.max}
                              onChange={(e) => updateGroup(def.name, { count: Number(e.target.value) })}
                              aria-invalid={Boolean(err("count"))}
                              style={{ width: 80 }}
                            />
                            {err("count") ? <div className="error-text">{err("count")}</div> : null}
                          </td>
                          <td>
                            <select
                              className="select"
                              aria-label={`${def.label}: machine type`}
                              value={group.machine_type}
                              onChange={(e) => updateGroup(def.name, { machine_type: e.target.value })}
                            >
                              {group.machine_type && !machines.some((m) => m.name === group.machine_type) ? (
                                <option value={group.machine_type}>{group.machine_type}</option>
                              ) : null}
                              {machines.map((m) => (
                                <option key={m.name} value={m.name}>
                                  {m.name} · {m.vcpus} vCPU · {m.memory_gb} GB{m.architecture === "arm64" ? " · Arm" : ""}
                                </option>
                              ))}
                            </select>
                            {err("machine_type") ? <div className="error-text">{err("machine_type")}</div> : null}
                          </td>
                          <td>
                            <input
                              className="input"
                              type="number"
                              aria-label={`${def.label}: disk size`}
                              min={engine.min_storage_gb}
                              max={engine.max_storage_gb}
                              value={group.storage_gb}
                              onChange={(e) => updateGroup(def.name, { storage_gb: Number(e.target.value) })}
                              aria-invalid={Boolean(err("storage_gb"))}
                              style={{ width: 100 }}
                            />
                            {err("storage_gb") ? <div className="error-text">{err("storage_gb")}</div> : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
            <div className="field-row">
                <Field label="Nodes" htmlFor="nodes" error={errorFor("node_count")}>
                  <input
                    id="nodes"
                    className="input"
                    type="number"
                    min={engine.min_nodes}
                    max={engine.max_nodes}
                    value={form.node_count}
                    onChange={(e) => update("node_count", Number(e.target.value))}
                    aria-invalid={Boolean(errorFor("node_count"))}
                  />
                </Field>
                <Field label="Storage per node (GB)" htmlFor="storage" error={errorFor("storage_gb")}>
                  <input
                    id="storage"
                    className="input"
                    type="number"
                    min={engine.min_storage_gb}
                    max={engine.max_storage_gb}
                    value={form.storage_gb}
                    onChange={(e) => update("storage_gb", Number(e.target.value))}
                    aria-invalid={Boolean(errorFor("storage_gb"))}
                  />
                </Field>
              </div>
            )}
            <Field label="Storage type" htmlFor="storage-type" error={errorFor("storage_type")}>
              <select
                id="storage-type"
                className="select"
                value={form.storage_type}
                onChange={(e) => update("storage_type", e.target.value)}
              >
                {catalog?.storage_types.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name} · {s.description}
                  </option>
                ))}
              </select>
            </Field>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.high_availability}
                onChange={(e) => {
                  update("high_availability", e.target.checked);
                  if (e.target.checked && !dedicated && form.node_count < engine.ha_min_nodes) {
                    update("node_count", engine.ha_min_nodes);
                  }
                }}
              />
              <span>
                <strong>High availability</strong>
                <span className="hint" style={{ display: "block" }}>
                  {dedicated
                    ? "One master per zone, data and coordinating nodes spread over three zones; replica shards stay in a different zone from their primary, and a lost zone never piles its replicas onto the others."
                    : `${engine.ha_min_nodes} master-eligible nodes in three zones; replica shards stay in a different zone from their primary.`}
                  {!haPossible ? " This network has fewer than three zones." : ""}
                </span>
                {errorFor("high_availability") ? <span className="error-text">{errorFor("high_availability")}</span> : null}
              </span>
            </label>
            {production && !form.high_availability ? (
              <Alert tone="warning" title="Production without high availability">
                A single zone or node failure makes this cluster unavailable.
              </Alert>
            ) : null}
          </div>
        </Card>
      </div>

      <aside className="card summary-panel">
        <div className="card-header">
          <h2>Summary</h2>
        </div>
        <div className="card-body stack">
          <dl className="kv" style={{ gridTemplateColumns: "1fr" }}>
            <div>
              <dt>Cluster</dt>
              <dd>{form.name || "-"}</dd>
            </div>
            <div>
              <dt>Environment</dt>
              <dd className="row" style={{ gap: 6 }}>
                {environment.name} <EnvironmentTypeBadge type={environment.type} />
              </dd>
            </div>
            <div>
              <dt>Network</dt>
              <dd className="break">
                {PROVIDER_NAMES[network.provider]} · {network.name} ({network.vpc}) · {network.region}
              </dd>
            </div>
            <div>
              <dt>Zones</dt>
              <dd>{haZones.join(", ")}</dd>
            </div>
            <div>
              <dt>Engine</dt>
              <dd>
                {engine.display_name} {form.version}
              </dd>
            </div>
            <div>
              <dt>Nodes</dt>
              {dedicated ? (
                <dd>
                  {groups.map((g) => (
                    <div key={g.name}>
                      {g.count} {g.name} × {g.machine_type}, {g.storage_gb} GB
                    </div>
                  ))}
                  <div className="muted small">{totalNodes} VMs</div>
                </dd>
              ) : (
                <dd>
                  {form.node_count} × {form.machine_type}
                  {machine ? ` (${machine.vcpus} vCPU, ${machine.memory_gb} GB)` : ""}
                </dd>
              )}
            </div>
            <div>
              <dt>Storage</dt>
              <dd>
                {form.storage_type} · {totalStorage} GB total
              </dd>
            </div>
            {dedicated ? (
              <div>
                <dt>Endpoint</dt>
                <dd>One private IP (internal load balancer, port 9200) in front of the coordinating nodes</dd>
              </div>
            ) : null}
          </dl>
          <p className="small muted">
            Runs in your subnet with no public IPs; only the cluster&apos;s own firewall rules are added. TLS between nodes
            and to clients, encrypted disks, credentials in your cloud&apos;s secret store.
          </p>
          <button
            className="button button-primary"
            type="submit"
            disabled={
              submitting ||
              !form.name ||
              (dedicated ? groups.some((g) => !g.machine_type) : !form.machine_type) ||
              Object.keys(clientErrors).length > 0
            }
          >
            {submitting ? "Creating..." : "Create cluster"}
          </button>
          {catalog?.simulated ? (
            <p className="small muted">Mock mode: provisioning is simulated and takes about 30 seconds.</p>
          ) : null}
        </div>
      </aside>
    </form>
  );
}
