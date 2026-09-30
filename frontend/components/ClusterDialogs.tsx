"use client";

import { useRef, useState } from "react";

import { api, newIdempotencyKey } from "@/lib/api";
import type { ClusterDetail, OperationAccepted } from "@/lib/types";

import { Alert, Dialog, ErrorAlert, Field } from "./ui";

export function ScaleDialog({
  cluster,
  onClose,
  onDone,
}: {
  cluster: ClusterDetail;
  onClose: () => void;
  onDone: (operationId: string) => void;
}) {
  const current = cluster.nodes.length || cluster.node_count;
  const [target, setTarget] = useState(current + (cluster.high_availability ? 2 : 1));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(newIdempotencyKey());
  // Scale-up only in this release: removing nodes needs shard relocation (docs/adr/0010).
  const invalid = target <= current || target > 30;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api<OperationAccepted>(`/clusters/${cluster.id}/scale`, {
        method: "POST",
        headers: { "Idempotency-Key": key.current },
        body: { node_count: target },
      });
      onDone(result.operation_id);
    } catch (err) {
      key.current = newIdempotencyKey();
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Scale up ${cluster.name}`}
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-primary" onClick={submit} disabled={busy || invalid}>
            {busy ? "Starting..." : `Scale to ${target} nodes`}
          </button>
        </>
      }
    >
      <ErrorAlert error={error} />
      <Field
        label="Number of nodes"
        htmlFor="scale-target"
        hint={`Currently ${current} node${current === 1 ? "" : "s"}. Choose a larger number; scaling down is not available yet.`}
      >
        <input
          id="scale-target"
          className="input"
          type="number"
          min={current + 1}
          max={30}
          value={target}
          onChange={(e) => setTarget(Number(e.target.value))}
        />
      </Field>
      {target > current ? (
        <p className="muted">
          {target - current} node{target - current === 1 ? "" : "s"} will be created with the same machine type and
          storage, then join the cluster. Shards rebalance automatically.
        </p>
      ) : (
        <Alert tone="warning" title="Only scaling up is supported">
          Removing nodes needs data to be moved off them first, which the platform does not do yet.
        </Alert>
      )}
    </Dialog>
  );
}

export function DeleteDialog({
  cluster,
  onClose,
  onDone,
}: {
  cluster: ClusterDetail;
  onClose: () => void;
  onDone: (operationId: string) => void;
}) {
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api<OperationAccepted>(`/clusters/${cluster.id}?confirm=${encodeURIComponent(confirm)}`, {
        method: "DELETE",
      });
      onDone(result.operation_id);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Delete ${cluster.name}`}
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-danger" onClick={submit} disabled={busy || confirm !== cluster.name}>
            {busy ? "Deleting..." : "Delete cluster"}
          </button>
        </>
      }
    >
      <ErrorAlert error={error} />
      <Alert tone="error" title="This permanently deletes the cluster and its data">
        All VMs, data disks, secrets, firewall rules and the network created for this cluster are destroyed in{" "}
        {cluster.project_id}. There is no backup in this release. Any operation still running on the cluster is
        stopped first, and a confirmed deletion cannot be cancelled.
      </Alert>
      <Field label={`Type ${cluster.name} to confirm`} htmlFor="delete-confirm">
        <input
          id="delete-confirm"
          className="input"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="off"
        />
      </Field>
    </Dialog>
  );
}

const FAULTS = [
  { value: "vm_down", label: "Stop the VM (VM unavailable)" },
  { value: "agent_down", label: "Stop the agent (agent unavailable)" },
  { value: "es_down", label: "Stop Elasticsearch on the node" },
  { value: "disk_pressure", label: "Fill the data disk (93%)" },
  { value: "heap_pressure", label: "Exhaust the JVM heap (94%)" },
  { value: "cpu_spike", label: "CPU spike (97%)" },
  { value: "clear", label: "Recover the node (clear all faults)" },
];

export function FaultDialog({
  cluster,
  onClose,
  onDone,
}: {
  cluster: ClusterDetail;
  onClose: () => void;
  onDone: () => void;
}) {
  const [node, setNode] = useState(cluster.nodes[1]?.name ?? cluster.nodes[0]?.name ?? "");
  const [fault, setFault] = useState("vm_down");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/mock/clusters/${cluster.id}/faults`, { method: "POST", body: { node_name: node, fault } });
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Simulate a failure"
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-primary" onClick={submit} disabled={busy || !node}>
            Apply
          </button>
        </>
      }
    >
      <p className="muted">
        Mock mode only. The fault is injected into the simulated VM; the control plane has to detect it through its
        normal monitoring (within about 15 seconds), exactly as it would for a real cluster.
      </p>
      <ErrorAlert error={error} />
      <div className="field-row">
        <Field label="Node" htmlFor="fault-node">
          <select id="fault-node" className="select" value={node} onChange={(e) => setNode(e.target.value)}>
            {cluster.nodes.map((n) => (
              <option key={n.name} value={n.name}>
                {n.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Failure" htmlFor="fault-kind">
          <select id="fault-kind" className="select" value={fault} onChange={(e) => setFault(e.target.value)}>
            {FAULTS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </Dialog>
  );
}
