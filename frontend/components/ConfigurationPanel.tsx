"use client";

import { useRef, useState } from "react";

import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import type { ClusterConfig, OperationAccepted, SettingDef, SettingValue } from "@/lib/types";

import { Icon } from "./Icon";
import { Alert, Card, Dialog, ErrorAlert, Spinner } from "./ui";

// Draft value per key; null removes the setting (back to Elasticsearch's default).
type Draft = Record<string, SettingValue | null>;

const HEAP = "byoc.jvm.heap_percent";
const KEY_RE = /^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$/;

function shown(value: SettingValue | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return typeof value === "boolean" ? (value ? "true" : "false") : String(value);
}

function range(setting: SettingDef): string {
  if (setting.kind === "enum" || setting.kind === "bool") return "";
  if (setting.minimum === null || setting.maximum === null) return "";
  const unit = setting.kind === "percent" ? "%" : "";
  if (setting.kind === "bytes") return "e.g. 40mb, 1gb";
  return `${setting.minimum}${unit} to ${setting.maximum}${unit}`;
}

function reservedKey(key: string, prefixes: string[]): boolean {
  return prefixes.some((p) => (p.endsWith(".") ? key.startsWith(p) : key === p || key.startsWith(`${p}.`)));
}

function SettingInput({
  setting,
  value,
  onChange,
  disabled,
}: {
  setting: SettingDef;
  value: SettingValue;
  onChange: (value: SettingValue) => void;
  disabled: boolean;
}) {
  const label = `${setting.key} value`;
  if (setting.kind === "bool") {
    return (
      <select
        className="select"
        aria-label={label}
        value={shown(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === "true")}
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (setting.kind === "enum") {
    return (
      <select className="select" aria-label={label} value={shown(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {setting.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      className="input mono"
      aria-label={label}
      value={shown(value)}
      disabled={disabled}
      placeholder={range(setting)}
      onChange={(e) => onChange(setting.kind === "int" && /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value)}
      style={{ maxWidth: 180 }}
    />
  );
}

/**
 * The cluster's configuration (docs/adr/0017, docs/adr/0018):
 * - elasticsearch.yml: any setting except those the platform owns; applied by a rolling restart,
 *   one node at a time, and rolled back on a node that does not start with it;
 * - live cluster settings: applied through the cluster settings API, no restart.
 */
export function ConfigurationPanel({
  clusterId,
  editable,
  blockedReason,
  onStarted,
}: {
  clusterId: string;
  editable: boolean;
  blockedReason: string | null;
  onStarted: (operationId: string) => void;
}) {
  const config = usePolling<ClusterConfig>((signal) => api(`/clusters/${clusterId}/config`, { signal }), 10000, [
    clusterId,
  ]);
  const [draft, setDraft] = useState<Draft>({});
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(newIdempotencyKey());

  const data = config.data;
  if (config.loading && !data) return <Spinner />;
  if (!data) return <ErrorAlert error={config.error} />;

  const byKey = new Map(data.settings.map((s) => [s.key, s]));
  const reserved = data.config_file?.reserved_prefixes ?? [];
  const locked = !editable || Boolean(blockedReason);

  // Current (desired) value of a key, and the value with the draft applied.
  const current = (k: string): SettingValue | undefined => data.desired[k] ?? byKey.get(k)?.default;
  const valueOf = (k: string): SettingValue | undefined => {
    if (!(k in draft)) return current(k);
    const pending = draft[k];
    return pending === null ? byKey.get(k)?.default : pending;
  };
  const changedKeys = Object.keys(draft).filter((k) => {
    if (draft[k] === null) return k in data.desired;
    return shown(valueOf(k)) !== shown(current(k));
  });
  const restart = changedKeys.some((k) => (byKey.get(k)?.scope ?? "static") === "static");
  const fieldErrors = error instanceof ApiError ? error.fields : {};
  const pending = [...data.pending.dynamic, ...data.pending.static];

  // elasticsearch.yml rows: typed settings, then custom ones (saved and newly added).
  const ymlCatalog = data.settings.filter((s) => s.scope === "static" && s.key !== HEAP);
  const heap = byKey.get(HEAP);
  const customKeys = Array.from(
    new Set([...Object.keys(data.custom ?? {}), ...Object.keys(draft).filter((k) => !byKey.has(k))]),
  ).sort();
  const liveSettings = data.settings.filter((s) => s.scope === "dynamic");

  const newKeyError = (() => {
    const k = newKey.trim();
    if (!k) return null;
    if (!KEY_RE.test(k)) return "Use a dotted lowercase name, e.g. indices.query.bool.max_clause_count.";
    if (reservedKey(k, reserved)) return "Managed by the platform (security, network, discovery, paths, node identity).";
    if (byKey.get(k)?.scope === "dynamic") return "This is a live setting: change it under Live cluster settings.";
    return null;
  })();

  const addSetting = () => {
    const k = newKey.trim();
    if (!k || newKeyError || !newValue.trim()) return;
    setDraft((d) => ({ ...d, [k]: newValue.trim() }));
    setNewKey("");
    setNewValue("");
  };

  const reset = (k: string) =>
    setDraft((d) => {
      const next = { ...d };
      if (k in data.desired) next[k] = null;
      else delete next[k];
      return next;
    });

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const settings = Object.fromEntries(changedKeys.map((k) => [k, draft[k] ?? null]));
      const result = await api<OperationAccepted>(`/clusters/${clusterId}/config`, {
        method: "PUT",
        headers: { "Idempotency-Key": key.current },
        body: { settings },
      });
      onStarted(result.operation_id);
    } catch (err) {
      key.current = newIdempotencyKey();
      setError(err);
      setReviewing(false);
      setBusy(false);
    }
  };

  // The rendered file for one node group, with the draft applied.
  const files = data.config_file?.files ?? [];
  const previewFile = files.find((f) => f.group === preview) ?? files[0];
  const draftLines = (() => {
    const entries = new Map<string, string>();
    for (const [k, v] of Object.entries(data.desired)) {
      if ((byKey.get(k)?.scope ?? "static") === "static" && k !== HEAP) entries.set(k, shown(v));
    }
    for (const k of Object.keys(draft)) {
      if ((byKey.get(k)?.scope ?? "static") !== "static" || k === HEAP) continue;
      if (draft[k] === null) entries.delete(k);
      else entries.set(k, shown(draft[k]));
    }
    return Array.from(entries.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}: ${JSON.stringify(v)}`);
  })();

  const row = (k: string, input: React.ReactNode, defaultValue: string, description?: string) => {
    const edited = changedKeys.includes(k);
    const removing = draft[k] === null;
    return (
      <tr key={k}>
        <td>
          <code>{k}</code>
          {edited ? <span className="pill" style={{ marginLeft: 6 }}>{removing ? "removing" : "edited"}</span> : null}
          {description ? <div className="small muted">{description}</div> : null}
          {fieldErrors[k] ? <div className="error-text">{fieldErrors[k]}</div> : null}
        </td>
        <td>{input}</td>
        <td className="mono muted">{defaultValue}</td>
        <td>
          {!locked && ((k in data.desired && !removing) || (edited && !removing)) ? (
            <button className="button button-small" title="Remove from the configuration" onClick={() => reset(k)}>
              <Icon name="trash" size={13} /> Remove
            </button>
          ) : null}
        </td>
      </tr>
    );
  };

  return (
    <Card
      title="Configuration"
      description="elasticsearch.yml and live cluster settings. Security, TLS, network, discovery, paths and node identity stay managed by the platform."
      actions={
        editable ? (
          <div className="row" style={{ gap: 8 }}>
            {changedKeys.length ? (
              <button className="button button-small" onClick={() => setDraft({})} disabled={busy}>
                Discard
              </button>
            ) : null}
            <button
              className="button button-primary button-small"
              onClick={() => setReviewing(true)}
              disabled={!changedKeys.length || Boolean(blockedReason) || busy}
            >
              Review {changedKeys.length || ""} change{changedKeys.length === 1 ? "" : "s"}
            </button>
          </div>
        ) : null
      }
      bodyless
    >
      <div className="card-body stack" style={{ gap: 8 }}>
        {error && !Object.keys(fieldErrors).length ? <ErrorAlert error={error} /> : null}
        {error && Object.keys(fieldErrors).length ? <ErrorAlert error={error} title="Some settings were rejected" /> : null}
        {!editable ? <p className="small muted">Only Owners and Admins can change the configuration.</p> : null}
        {editable && blockedReason ? <p className="small muted">{blockedReason}</p> : null}
        {pending.length ? (
          <Alert tone="info" title="Not applied to every node yet">
            {pending.join(", ")}. A failed change leaves the nodes on their previous configuration: correct or remove
            the setting and apply again.
          </Alert>
        ) : null}
      </div>

      {/* ------------------------------------------------------------ elasticsearch.yml */}
      <div className="card-body stack" style={{ gap: 6, paddingBottom: 0 }}>
        <div className="spread">
          <h3 style={{ margin: 0 }}>
            elasticsearch.yml <span className="muted small mono">{data.config_file?.path}</span>
          </h3>
          {files.length ? (
            <div className="segmented" role="group" aria-label="Preview">
              <button type="button" aria-pressed={preview === null} onClick={() => setPreview(null)}>
                Settings
              </button>
              {files.map((f) => (
                <button key={f.group} type="button" aria-pressed={preview === f.group} onClick={() => setPreview(f.group)}>
                  {files.length > 1 ? `${f.group} file` : "Full file"}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Changes restart the nodes one at a time (data, then coordinating, then masters; the elected master last). A
          node that does not start with a setting is put back on its previous file and the change stops there.
        </p>
      </div>

      {preview !== null && previewFile ? (
        <div className="card-body">
          <pre className="json" aria-label={`elasticsearch.yml for ${previewFile.group} nodes`}>
            <span className="muted">
              {"# Managed by the platform\n"}
              {previewFile.managed.join("\n")}
            </span>
            {"\n# Your settings\n"}
            {draftLines.length ? draftLines.join("\n") : "# (none)"}
          </pre>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: "45%" }}>Setting</th>
                <th>Value</th>
                <th>Default</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {ymlCatalog.map((s) =>
                row(
                  s.key,
                  <SettingInput
                    setting={s}
                    value={valueOf(s.key) ?? s.default}
                    disabled={locked || busy}
                    onChange={(v) => setDraft((d) => ({ ...d, [s.key]: v }))}
                  />,
                  shown(s.default),
                  s.description,
                ),
              )}
              {customKeys.map((k) =>
                row(
                  k,
                  <input
                    className="input mono"
                    aria-label={`${k} value`}
                    value={draft[k] === null ? "" : shown(valueOf(k))}
                    placeholder={draft[k] === null ? "removed" : ""}
                    disabled={locked || busy || draft[k] === null}
                    onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                    style={{ maxWidth: 260 }}
                  />,
                  "Elasticsearch default",
                ),
              )}
              {!locked ? (
                <tr>
                  <td>
                    <input
                      className="input mono"
                      aria-label="New setting name"
                      placeholder="setting.name, e.g. indices.query.bool.max_clause_count"
                      value={newKey}
                      onChange={(e) => setNewKey(e.target.value.trim().toLowerCase())}
                      onKeyDown={(e) => e.key === "Enter" && addSetting()}
                      aria-invalid={Boolean(newKeyError)}
                    />
                    {newKeyError ? <div className="error-text">{newKeyError}</div> : null}
                  </td>
                  <td>
                    <input
                      className="input mono"
                      aria-label="New setting value"
                      placeholder="value"
                      value={newValue}
                      onChange={(e) => setNewValue(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addSetting()}
                      style={{ maxWidth: 260 }}
                    />
                  </td>
                  <td colSpan={2}>
                    <button
                      className="button button-small"
                      onClick={addSetting}
                      disabled={!newKey || !newValue.trim() || Boolean(newKeyError)}
                    >
                      <Icon name="plus" size={13} /> Add setting
                    </button>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}

      {/* ------------------------------------------------------------------ JVM heap */}
      {heap ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: "45%" }}>
                  JVM <span className="muted small">· jvm.options.d, rolling restart</span>
                </th>
                <th>Value</th>
                <th>Default</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {row(
                HEAP,
                <SettingInput
                  setting={heap}
                  value={valueOf(HEAP) ?? heap.default}
                  disabled={locked || busy}
                  onChange={(v) => setDraft((d) => ({ ...d, [HEAP]: v }))}
                />,
                shown(heap.default),
                heap.description,
              )}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* ------------------------------------------------------------ live settings */}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: "45%" }}>
                Live cluster settings <span className="muted small">· cluster settings API, no restart</span>
              </th>
              <th>Value</th>
              <th>Default</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {liveSettings.map((s) =>
              row(
                s.key,
                <SettingInput
                  setting={s}
                  value={valueOf(s.key) ?? s.default}
                  disabled={locked || busy}
                  onChange={(v) => setDraft((d) => ({ ...d, [s.key]: v }))}
                />,
                shown(s.default),
                s.description,
              ),
            )}
          </tbody>
        </table>
      </div>

      {reviewing ? (
        <Dialog
          title="Apply configuration changes"
          onClose={() => setReviewing(false)}
          footer={
            <>
              <button className="button" onClick={() => setReviewing(false)}>
                Cancel
              </button>
              <button className="button button-primary" onClick={submit} disabled={busy}>
                {busy ? "Starting..." : restart ? "Apply with rolling restart" : "Apply now"}
              </button>
            </>
          }
        >
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Setting</th>
                  <th>From</th>
                  <th>To</th>
                </tr>
              </thead>
              <tbody>
                {changedKeys.map((k) => {
                  const live = byKey.get(k)?.scope === "dynamic";
                  return (
                    <tr key={k}>
                      <td>
                        <code>{k}</code>
                        <div className="small muted">
                          {live ? "live" : k === HEAP ? "JVM, rolling restart" : "elasticsearch.yml, rolling restart"}
                        </div>
                      </td>
                      <td className="mono">{k in data.desired ? shown(data.desired[k]) : shown(byKey.get(k)?.default ?? "(not set)")}</td>
                      <td className="mono">
                        {draft[k] === null ? <span className="muted">(removed)</span> : shown(valueOf(k))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {restart ? (
            <Alert tone="warning" title="Every node restarts, one at a time">
              Data nodes first, then coordinating nodes, then the masters with the elected master last. The platform
              waits for the cluster to be healthy after each node. If a node does not start with the new file (for
              example an unknown setting), it is put back on its previous file and the remaining nodes are not touched.
            </Alert>
          ) : (
            <p className="muted">Applied to the running cluster by the elected master. No node restarts.</p>
          )}
        </Dialog>
      ) : null}
    </Card>
  );
}
