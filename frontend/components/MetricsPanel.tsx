"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { formatClock, formatNumber, formatPercent } from "@/lib/format";
import { usePolling } from "@/lib/hooks";
import type { ClusterMetrics, MetricSample } from "@/lib/types";

import { LineChart, type Point } from "./LineChart";
import { Card, ErrorAlert } from "./ui";

interface MetricSpec {
  key: keyof MetricSample;
  label: string;
  percent: boolean;
  unit?: string;
}

const METRICS: MetricSpec[] = [
  { key: "cpu_percent", label: "CPU", percent: true },
  { key: "memory_percent", label: "Memory", percent: true },
  { key: "disk_percent", label: "Disk", percent: true },
  { key: "jvm_heap_percent", label: "JVM heap", percent: true },
  { key: "search_rate", label: "Search rate", percent: false, unit: "queries/s" },
  { key: "indexing_rate", label: "Indexing rate", percent: false, unit: "docs/s" },
];

const WINDOWS = [
  { minutes: 15, label: "15 min" },
  { minutes: 60, label: "1 hour" },
  { minutes: 360, label: "6 hours" },
];

function valueOf(sample: MetricSample, key: keyof MetricSample): number | null {
  const value = sample[key];
  return typeof value === "number" ? value : null;
}

function format(spec: MetricSpec, value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return spec.percent ? formatPercent(value) : `${formatNumber(value)}${spec.unit ? ` ${spec.unit}` : ""}`;
}

/** Cluster metrics as small multiples (one unit per chart, one axis each) plus a table view. */
export function MetricsPanel({ clusterId }: { clusterId: string }) {
  const [minutes, setMinutes] = useState(60);
  const [view, setView] = useState<"charts" | "table">("charts");
  const { data, error } = usePolling<ClusterMetrics>(
    (signal) => api(`/clusters/${clusterId}/metrics?minutes=${minutes}`, { signal }),
    10000,
    [clusterId, minutes],
  );
  const history = data?.history ?? [];
  const latest = history[history.length - 1];

  return (
    <Card
      title="Metrics"
      description="Averaged across nodes; disk is used space over total capacity."
      actions={
        <>
          <div className="segmented" role="group" aria-label="Time range">
            {WINDOWS.map((w) => (
              <button key={w.minutes} type="button" aria-pressed={minutes === w.minutes} onClick={() => setMinutes(w.minutes)}>
                {w.label}
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "charts"} onClick={() => setView("charts")}>
              Charts
            </button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        </>
      }
    >
      <ErrorAlert error={error} />
      {view === "charts" ? (
        <div className="grid grid-3">
          {METRICS.map((spec) => {
            const points: Point[] = history.map((s) => ({ t: new Date(s.timestamp).getTime(), v: valueOf(s, spec.key) }));
            return (
              <div key={spec.key} className="card metric-card" style={{ boxShadow: "none" }}>
                <div className="metric-head">
                  <span className="metric-title">{spec.label}</span>
                  <span className="metric-value">{latest ? format(spec, valueOf(latest, spec.key)) : "-"}</span>
                </div>
                <LineChart
                  points={points}
                  label={spec.label}
                  yMax={spec.percent ? 100 : undefined}
                  format={(v) => format(spec, v)}
                />
              </div>
            );
          })}
        </div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: 420 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Time</th>
                {METRICS.map((spec) => (
                  <th key={spec.key} className="num">
                    {spec.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...history].reverse().map((sample) => (
                <tr key={sample.timestamp}>
                  <td className="nums">{formatClock(sample.timestamp)}</td>
                  {METRICS.map((spec) => (
                    <td key={spec.key} className="num">
                      {format(spec, valueOf(sample, spec.key))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {history.length === 0 ? <p className="muted" style={{ padding: 14 }}>No samples in this time range yet.</p> : null}
        </div>
      )}
    </Card>
  );
}
