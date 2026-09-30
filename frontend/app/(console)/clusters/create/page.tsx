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
  EngineCatalog,
  Environment,
  MachineType,
  Network,
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
}

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
  });
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
    if (form.high_availability && form.node_count < engine.ha_min_nodes) {
      errors.node_count = `High availability needs at least ${engine.ha_min_nodes} nodes.`;
    }
    if (form.high_availability && !haPossible) {
      errors.high_availability = `This network has ${network.zones.length} zone(s); high availability needs 3.`;
    }
    if (form.storage_gb < engine.min_storage_gb) errors.storage_gb = `At least ${engine.min_storage_gb} GB.`;
    if (machine && machine.memory_gb < engine.min_memory_gb) {
      errors.machine_type = `${engine.display_name} needs at least ${engine.min_memory_gb} GB of memory.`;
    }
    return errors;
  }, [form, engine, machine, haPossible, network.zones.length]);
  const errorFor = (field: string) => clientErrors[field] ?? fieldErrors[field];
  const update = <K extends keyof SpecState>(key: K, value: SpecState[K]) => setForm((f) => ({ ...f, [key]: value }));
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
        body: { ...form, environment_id: environment.id, network_id: network.id },
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
            </div>
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
                  if (e.target.checked && form.node_count < engine.ha_min_nodes) update("node_count", engine.ha_min_nodes);
                }}
              />
              <span>
                <strong>High availability</strong>
                <span className="hint" style={{ display: "block" }}>
                  {engine.ha_min_nodes} master-eligible nodes in three zones; replica shards stay in a different zone from
                  their primary.
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
              <dd>
                {form.node_count} × {form.machine_type}
                {machine ? ` (${machine.vcpus} vCPU, ${machine.memory_gb} GB)` : ""}
              </dd>
            </div>
            <div>
              <dt>Storage</dt>
              <dd>
                {form.storage_gb} GB {form.storage_type} per node · {form.storage_gb * form.node_count} GB total
              </dd>
            </div>
          </dl>
          <p className="small muted">
            Runs in your subnet with no public IPs; only the cluster&apos;s own firewall rules are added. TLS between nodes
            and to clients, encrypted disks, credentials in your cloud&apos;s secret store.
          </p>
          <button
            className="button button-primary"
            type="submit"
            disabled={submitting || !form.name || !form.machine_type || Object.keys(clientErrors).length > 0}
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
