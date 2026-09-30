"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

import { ApiError } from "@/lib/api";

import { Icon, type IconName } from "./Icon";

export function PageHeader({
  title,
  subtitle,
  actions,
  breadcrumb,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {breadcrumb ? <nav className="breadcrumb" aria-label="Breadcrumb">{breadcrumb}</nav> : null}
        <h1>{title}</h1>
        {subtitle ? <p className="subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="actions">{actions}</div> : null}
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  bodyless = false,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  bodyless?: boolean;
}) {
  return (
    <section className="card">
      {title ? (
        <div className="card-header">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      ) : null}
      {bodyless ? children : <div className="card-body">{children}</div>}
    </section>
  );
}

export function ErrorAlert({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : null;
  const message = apiError?.message ?? (error instanceof Error ? error.message : String(error));
  return (
    <div className="alert alert-error" role="alert">
      <Icon name="alertOctagon" />
      <div>
        <div className="alert-title">{title ?? message}</div>
        {title ? <div className="alert-detail">{message}</div> : null}
        {apiError?.reason ? <div className="alert-detail">Reason: {apiError.reason}</div> : null}
        {apiError?.suggestedAction ? <div className="alert-detail">Suggested action: {apiError.suggestedAction}</div> : null}
        {apiError?.requestId ? <div className="alert-detail small">Request ID: {apiError.requestId}</div> : null}
      </div>
    </div>
  );
}

export function Alert({
  tone,
  title,
  children,
  icon,
}: {
  tone: "info" | "warning" | "error" | "success";
  title: ReactNode;
  children?: ReactNode;
  icon?: IconName;
}) {
  const defaults: Record<typeof tone, IconName> = {
    info: "info",
    warning: "alertTriangle",
    error: "alertOctagon",
    success: "checkCircle",
  };
  return (
    <div className={`alert alert-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon name={icon ?? defaults[tone]} />
      <div>
        <div className="alert-title">{title}</div>
        {children ? <div className="alert-detail">{children}</div> : null}
      </div>
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon: IconName; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={28} />
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="row muted" role="status">
      <Icon name="loader" className="spinner" />
      {label}
    </span>
  );
}

export function Dialog({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])");
    first?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <div className="dialog-header">
          <h2 id={titleId}>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close" data-close>
            <Icon name="x" />
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer ? <div className="dialog-footer">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {error ? (
        <div className="error-text" id={htmlFor ? `${htmlFor}-error` : undefined}>
          {error}
        </div>
      ) : hint ? (
        <div className="hint">{hint}</div>
      ) : null}
    </div>
  );
}

export function Copyable({ value }: { value: string }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      <code>{value}</code>
      <button
        type="button"
        className="icon-button"
        style={{ width: 24, height: 24 }}
        aria-label="Copy to clipboard"
        onClick={() => void navigator.clipboard?.writeText(value)}
      >
        <Icon name="copy" size={13} />
      </button>
    </span>
  );
}
