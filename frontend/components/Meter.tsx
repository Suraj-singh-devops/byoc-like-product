import { formatPercent } from "@/lib/format";

import { Icon } from "./Icon";

interface Thresholds {
  warning: number;
  critical: number;
}

const DEFAULT_THRESHOLDS: Thresholds = { warning: 80, critical: 90 };

function level(value: number, thresholds: Thresholds): "normal" | "warning" | "critical" {
  if (value >= thresholds.critical) return "critical";
  if (value >= thresholds.warning) return "warning";
  return "normal";
}

/** A ratio against 100%. The fill carries severity; the track is a lighter step of the same hue. */
export function Meter({
  label,
  value,
  thresholds = DEFAULT_THRESHOLDS,
}: {
  label: string;
  value: number | null | undefined;
  thresholds?: Thresholds;
}) {
  if (value === null || value === undefined) {
    return (
      <div className="meter-row">
        <span className="meter-label">{label}</span>
        <div className="meter" aria-hidden="true" />
        <span className="meter-value meter-empty">-</span>
      </div>
    );
  }
  const clamped = Math.max(0, Math.min(100, value));
  const severity = level(clamped, thresholds);
  return (
    <div className="meter-row">
      <span className="meter-label">{label}</span>
      <div
        className="meter"
        data-level={severity}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped)}
        aria-valuetext={`${formatPercent(clamped)}${severity === "normal" ? "" : `, ${severity}`}`}
      >
        <span style={{ width: `${clamped}%` }} />
      </div>
      <span className="meter-value">
        {severity === "critical" ? (
          <Icon name="alertOctagon" size={13} style={{ color: "var(--critical)" }} title="Critical" />
        ) : severity === "warning" ? (
          <Icon name="alertTriangle" size={13} style={{ color: "var(--warning)" }} title="High" />
        ) : null}
        {formatPercent(clamped)}
      </span>
    </div>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className="progress"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
    >
      <span style={{ width: `${clamped}%` }} />
    </div>
  );
}
