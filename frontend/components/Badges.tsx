import type {
  AgentStatus,
  CloudProviderName,
  ClusterHealth,
  ClusterLifecycle,
  EnvironmentType,
  Health,
  NetworkStatus,
  OperationStatus,
} from "@/lib/types";
import { humanize } from "@/lib/format";

import { Icon, type IconName } from "./Icon";

type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "accent";

// Status is never conveyed by color alone: every badge carries an icon and a text label.
function Badge({ tone, icon, label, spin = false }: { tone: Tone; icon: IconName; label: string; spin?: boolean }) {
  return (
    <span className={`badge badge-${tone}`}>
      <Icon name={icon} size={14} className={spin ? "spinner" : undefined} />
      {label}
    </span>
  );
}

const HEALTH: Record<Health, { tone: Tone; icon: IconName; label: string }> = {
  HEALTHY: { tone: "good", icon: "checkCircle", label: "Healthy" },
  DEGRADED: { tone: "warning", icon: "alertTriangle", label: "Degraded" },
  UNHEALTHY: { tone: "critical", icon: "alertOctagon", label: "Unhealthy" },
  UNKNOWN: { tone: "neutral", icon: "helpCircle", label: "Unknown" },
};

const TONE_COLOR: Record<Tone, string> = {
  good: "var(--good)",
  warning: "var(--warning)",
  critical: "var(--critical)",
  neutral: "var(--text-muted)",
  serious: "var(--serious)",
  accent: "var(--accent)",
};

export function HealthBadge({ health }: { health: Health }) {
  const spec = HEALTH[health] ?? HEALTH.UNKNOWN;
  return <Badge {...spec} />;
}

export function HealthIcon({ health, size = 16 }: { health: Health; size?: number }) {
  const spec = HEALTH[health] ?? HEALTH.UNKNOWN;
  return <Icon name={spec.icon} size={size} style={{ color: TONE_COLOR[spec.tone] }} title={spec.label} />;
}

export function LifecycleBadge({ lifecycle }: { lifecycle: ClusterLifecycle }) {
  switch (lifecycle) {
    case "ACTIVE":
      return <Badge tone="good" icon="zap" label="Active" />;
    case "CREATING":
    case "SCALING":
    case "UPGRADING":
    case "DELETING":
      return <Badge tone="accent" icon="loader" label={humanize(lifecycle)} spin />;
    case "FAILED":
      return <Badge tone="critical" icon="alertOctagon" label="Failed" />;
    case "DELETED":
      return <Badge tone="neutral" icon="trash" label="Deleted" />;
    default:
      return <Badge tone="neutral" icon="helpCircle" label={humanize(lifecycle)} />;
  }
}

/**
 * One status for lists and cards, derived from the two stored fields: the lifecycle while an
 * operation or a failure is in progress, the health while the cluster is ACTIVE.
 */
export function ClusterStatusBadge({ lifecycle, health }: { lifecycle: ClusterLifecycle; health: ClusterHealth }) {
  if (lifecycle !== "ACTIVE") return <LifecycleBadge lifecycle={lifecycle} />;
  if (health === "UNKNOWN") return <LifecycleBadge lifecycle="ACTIVE" />;
  return <HealthBadge health={health} />;
}

export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  if (status === "REPORTING") return <Badge tone="good" icon="activity" label="Reporting" />;
  if (status === "STALE") return <Badge tone="warning" icon="alertTriangle" label="Stale" />;
  return <Badge tone="neutral" icon="clock" label="Not reported" />;
}

export function OperationStatusBadge({ status }: { status: OperationStatus }) {
  switch (status) {
    case "COMPLETED":
      return <Badge tone="good" icon="checkCircle" label="Completed" />;
    case "FAILED":
      return <Badge tone="critical" icon="alertOctagon" label="Failed" />;
    case "CANCELLED":
      return <Badge tone="neutral" icon="slash" label="Cancelled" />;
    case "PENDING":
      return <Badge tone="neutral" icon="clock" label="Pending" />;
    default:
      return <Badge tone="accent" icon="loader" label={humanize(status)} spin />;
  }
}

export function SeverityIcon({ severity }: { severity: "INFO" | "WARNING" | "CRITICAL" }) {
  if (severity === "CRITICAL") return <Icon name="alertOctagon" style={{ color: "var(--critical)" }} title="Critical" />;
  if (severity === "WARNING") return <Icon name="alertTriangle" style={{ color: "var(--warning)" }} title="Warning" />;
  return <Icon name="info" style={{ color: "var(--accent)" }} title="Info" />;
}

export function AuditStatusBadge({ status }: { status: string }) {
  if (status === "FAILURE") return <Badge tone="critical" icon="alertOctagon" label="Failure" />;
  return <Badge tone="good" icon="checkCircle" label="Success" />;
}

export function EnvironmentTypeBadge({ type }: { type: EnvironmentType }) {
  if (type === "PRODUCTION") return <Badge tone="serious" icon="shield" label="Production" />;
  return <Badge tone="neutral" icon="flask" label="Test" />;
}

export function NetworkStatusBadge({ status }: { status: NetworkStatus }) {
  switch (status) {
    case "AVAILABLE":
      return <Badge tone="good" icon="checkCircle" label="Available" />;
    case "FAILED":
      return <Badge tone="critical" icon="alertOctagon" label="Failed" />;
    case "UNAVAILABLE":
      return <Badge tone="warning" icon="alertTriangle" label="Unavailable" />;
    case "VALIDATING":
      return <Badge tone="accent" icon="loader" label="Validating" spin />;
    default:
      return <Badge tone="neutral" icon="clock" label="Pending" />;
  }
}

export const PROVIDER_NAMES: Record<CloudProviderName, string> = { gcp: "Google Cloud", aws: "AWS" };

export function ProviderBadge({ provider }: { provider: CloudProviderName }) {
  return <Badge tone="neutral" icon="cloud" label={PROVIDER_NAMES[provider] ?? provider.toUpperCase()} />;
}
