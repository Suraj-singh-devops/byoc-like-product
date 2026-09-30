"use client";

import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "@/lib/api";
import type { CloudAccount, CloudOnboarding, CloudProvider, CloudProviderName } from "@/lib/types";

import { Icon } from "./Icon";
import { Alert, Copyable, Dialog, ErrorAlert, Field } from "./ui";

// Regions of the platform's catalogs, suggested while typing; the API validates the value.
export const REGIONS: Record<CloudProviderName, string[]> = {
  gcp: [
    "asia-south1",
    "asia-south2",
    "asia-southeast1",
    "europe-west1",
    "europe-west4",
    "us-central1",
    "us-east1",
    "us-west1",
  ],
  aws: ["ap-south-1", "ap-southeast-1", "eu-central-1", "eu-west-1", "us-east-1", "us-east-2", "us-west-2"],
};

/** The three clouds of the console. Azure is shown as planned and cannot be chosen. */
export function ProviderPicker({
  value,
  onChange,
}: {
  value: CloudProviderName | null;
  onChange: (provider: CloudProviderName) => void;
}) {
  const options: { name: CloudProviderName | "azure"; label: string; hint: string }[] = [
    { name: "gcp", label: "Google Cloud", hint: "Compute Engine in your project" },
    { name: "aws", label: "Amazon Web Services", hint: "EC2 in your account" },
    { name: "azure", label: "Microsoft Azure", hint: "Coming later" },
  ];
  return (
    <div className="choice-grid" role="radiogroup" aria-label="Cloud provider">
      {options.map((option) => {
        const disabled = option.name === "azure";
        const selected = option.name === value;
        return (
          <button
            key={option.name}
            type="button"
            role="radio"
            aria-checked={selected}
            className="choice"
            data-selected={selected || undefined}
            disabled={disabled}
            onClick={() => !disabled && onChange(option.name as CloudProviderName)}
          >
            <Icon name="cloud" size={18} />
            <span>
              <strong>{option.label}</strong>
              <span className="muted small">{option.hint}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** How to grant the platform access: the principal, and for AWS the external ID and trust policy. */
export function OnboardingPanel({ provider }: { provider: CloudProviderName }) {
  const [info, setInfo] = useState<CloudOnboarding | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<CloudOnboarding>(`/cloud-accounts/onboarding?provider=${provider}`)
      .then((result) => !cancelled && setInfo(result))
      .catch(() => !cancelled && setInfo(null));
    return () => {
      cancelled = true;
    };
  }, [provider]);
  if (!info) return null;
  if (provider === "aws") {
    return (
      <Alert tone="info" title="Keyless access: the platform assumes a role">
        <div className="stack" style={{ gap: 6 }}>
          <span>
            Create role <code>{info.role_name}</code> with the platform&apos;s policy. Its trust policy must allow only
            your organization&apos;s platform role, with your external ID:
          </span>
          <span>
            External ID <Copyable value={info.external_id ?? ""} />
          </span>
          <details>
            <summary>Trust policy</summary>
            <pre className="code-block">{JSON.stringify(info.trust_policy, null, 2)}</pre>
          </details>
          <span className="small muted">
            No access keys are accepted. The principal&apos;s account is assigned when real AWS access is enabled.
          </span>
        </div>
      </Alert>
    );
  }
  return (
    <Alert tone="info" title="Keyless access: the platform impersonates a service account">
      Grant your organization&apos;s platform identity <code>roles/iam.serviceAccountTokenCreator</code> on the service
      account (see docs/gcp-setup.md). Service-account keys are not accepted.
    </Alert>
  );
}

export function CloudAccountDialog({
  provider: fixedProvider,
  mock,
  onClose,
  onCreated,
}: {
  provider?: CloudProviderName;
  mock: boolean;
  onClose: () => void;
  onCreated: (account: CloudAccount) => void;
}) {
  const [provider, setProvider] = useState<CloudProviderName>(fixedProvider ?? "gcp");
  const [name, setName] = useState("");
  const [project, setProject] = useState("");
  const [region, setRegion] = useState(REGIONS[fixedProvider ?? "gcp"][0]);
  const [principal, setPrincipal] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<CloudProvider[]>([]);
  useEffect(() => {
    api<CloudProvider[]>("/cloud-providers").then(setProviders).catch(() => setProviders([]));
  }, []);
  const fields = error instanceof ApiError ? error.fields : {};
  const aws = provider === "aws";
  // Development only (docs/adr/0015): the platform uses the developer's own credentials.
  const local = providers.find((p) => p.name === provider)?.auth_type === "local_credentials";
  const suggested = aws
    ? `arn:aws:iam::${project.replace(/[\s-]/g, "") || "123456789012"}:role/db-platform-provisioner`
    : `db-platform-provisioner@${project || "your-project"}.iam.gserviceaccount.com`;

  const choose = (next: CloudProviderName) => {
    setProvider(next);
    setRegion(REGIONS[next][0]);
    setPrincipal("");
    setError(null);
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = await api<CloudAccount>("/cloud-accounts", {
        method: "POST",
        body: {
          name,
          provider,
          project_id: project,
          region,
          ...(local ? {} : aws ? { role_arn: principal } : { service_account_email: principal }),
          validate_now: true,
        },
      });
      onCreated(account);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={aws ? "Add an AWS account" : fixedProvider ? "Add a GCP project" : "Add a cloud account"}
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button button-primary"
            onClick={() => void submit()}
            disabled={busy || !name || !project || !region || (!local && !principal)}
          >
            {busy ? "Validating..." : "Add and validate"}
          </button>
        </>
      }
    >
      <ErrorAlert error={error} />
      <form className="form" onSubmit={submit}>
        {fixedProvider ? null : <ProviderPicker value={provider} onChange={choose} />}
        <div className="field-row">
          <Field label="Name" htmlFor="account-name" error={fields.name}>
            <input
              id="account-name"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={aws ? "production-aws" : "production"}
            />
          </Field>
          <Field label={aws ? "AWS account ID" : "GCP project ID"} htmlFor="account-project" error={fields.project_id}>
            <input
              id="account-project"
              className="input"
              value={project}
              onChange={(e) => setProject(e.target.value.trim())}
              placeholder={aws ? "123456789012" : "customer-prod"}
              inputMode={aws ? "numeric" : undefined}
            />
          </Field>
        </div>
        <Field label="Default region" htmlFor="account-region" error={fields.region} hint="Suggested when registering networks.">
          <input
            id="account-region"
            className="input"
            list={`account-regions-${provider}`}
            value={region}
            onChange={(e) => setRegion(e.target.value.trim())}
          />
          <datalist id={`account-regions-${provider}`}>
            {REGIONS[provider].map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </Field>
        {local ? (
          <Alert tone="warning" title="Development mode: your own Google credentials">
            The platform uses the application default credentials of the machine running it, directly and only
            for the allowlisted sandbox projects. Real infrastructure is created and billed to the project.
          </Alert>
        ) : (
        <Field
          label={aws ? "Role to assume (ARN)" : "Service account to impersonate"}
          htmlFor="account-principal"
          error={aws ? fields.role_arn : fields.service_account_email}
          hint={
            aws
              ? "A role in this account with the platform's policy, trusting your organization with your external ID."
              : "A service account in this project with the platform's custom role. No key is uploaded."
          }
        >
          <input
            id="account-principal"
            className="input"
            value={principal}
            onChange={(e) => setPrincipal(e.target.value.trim())}
            placeholder={suggested}
          />
          {mock && project && !principal ? (
            <button type="button" className="button button-small" onClick={() => setPrincipal(suggested)}>
              Use {suggested}
            </button>
          ) : null}
        </Field>
        )}
        {local ? null : <OnboardingPanel provider={provider} />}
        {mock ? (
          <Alert tone="info" title="Mock mode">
            Validation is simulated.{" "}
            {aws ? (
              <>
                Role ARNs containing <code>untrusted</code> or <code>denied</code> show the assume-role and
                missing-permission errors.
              </>
            ) : (
              <>
                Project IDs containing <code>denied</code>, <code>disabled</code> or <code>notfound</code> show the
                missing-permission, disabled-API and unknown-project errors.
              </>
            )}
          </Alert>
        ) : null}
      </form>
    </Dialog>
  );
}
