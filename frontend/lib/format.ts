const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const absolute = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "medium" });
const clock = new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" });
const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en");

export function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatAbsolute(value: string | Date | null | undefined): string {
  const date = toDate(value);
  return date ? absolute.format(date) : "-";
}

export function formatClock(value: string | Date | null | undefined): string {
  const date = toDate(value);
  return date ? clock.format(date) : "-";
}

export function formatRelative(value: string | Date | null | undefined, now = Date.now()): string {
  const date = toDate(value);
  if (!date) return "-";
  const seconds = Math.round((date.getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 10) return "just now";
  if (abs < 60) return relative.format(seconds, "second");
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relative.format(Math.round(seconds / 3600), "hour");
  return relative.format(Math.round(seconds / 86400), "day");
}

export function formatDuration(start: string | null | undefined, end?: string | null): string {
  const from = toDate(start);
  if (!from) return "-";
  const to = toDate(end) ?? new Date();
  const total = Math.max(0, Math.round((to.getTime() - from.getTime()) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

export function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return Math.abs(value) >= 10000 ? compact.format(value) : integer.format(Math.round(value));
}

export function formatRate(value: number | null | undefined, unit = "/s"): string {
  if (value === null || value === undefined) return "-";
  return `${formatNumber(value)}${unit}`;
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

export function formatBytesRate(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${formatBytes(value)}/s`;
}

/** "SCALE_CLUSTER" -> "Scale cluster" */
export function humanize(value: string | null | undefined): string {
  if (!value) return "-";
  const text = value.replace(/[_-]+/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function roleLabel(role: string): string {
  return humanize(role);
}

export function engineLabel(engine: string, version?: string): string {
  const name = engine === "elasticsearch" ? "Elasticsearch" : humanize(engine);
  return version ? `${name} ${version}` : name;
}

/** Copy of an object with the given keys first (JSONB does not keep key order). */
export function withKeyOrder(value: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of keys) if (key in value) out[key] = value[key];
  for (const [key, item] of Object.entries(value)) if (!(key in out)) out[key] = item;
  return out;
}
