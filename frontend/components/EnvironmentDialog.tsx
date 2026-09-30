"use client";

import { useState, type FormEvent } from "react";

import { api, ApiError } from "@/lib/api";
import type { Environment, EnvironmentType } from "@/lib/types";

import { Icon } from "./Icon";
import { Dialog, ErrorAlert, Field } from "./ui";

export function EnvironmentDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (environment: Environment) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<EnvironmentType>("TEST");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const fields = error instanceof ApiError ? error.fields : {};

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(
        await api<Environment>("/environments", {
          method: "POST",
          body: { name, type, description: description || null },
        }),
      );
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Create an environment"
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-primary" onClick={() => void submit()} disabled={busy || !name}>
            {busy ? "Creating..." : "Create environment"}
          </button>
        </>
      }
    >
      <ErrorAlert error={error} />
      <form className="form" onSubmit={submit}>
        <div className="choice-grid" role="radiogroup" aria-label="Environment type">
          {(
            [
              ["TEST", "Test", "Development, staging, experiments", "flask"],
              ["PRODUCTION", "Production", "Live workloads; high availability suggested", "shield"],
            ] as const
          ).map(([value, label, hint, icon]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={type === value}
              className="choice"
              data-selected={type === value || undefined}
              onClick={() => {
                setType(value);
                if (!name || name === "test" || name === "production") setName(value.toLowerCase());
              }}
            >
              <Icon name={icon} size={18} />
              <span>
                <strong>{label}</strong>
                <span className="muted small">{hint}</span>
              </span>
            </button>
          ))}
        </div>
        <Field
          label="Name"
          htmlFor="environment-name"
          error={fields.name}
          hint="Lowercase letters, digits and hyphens, e.g. production or test-eu."
        >
          <input
            id="environment-name"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value.trim().toLowerCase())}
            placeholder="production"
          />
        </Field>
        <Field label="Description (optional)" htmlFor="environment-description" error={fields.description}>
          <input
            id="environment-description"
            className="input"
            value={description}
            maxLength={200}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
      </form>
    </Dialog>
  );
}
