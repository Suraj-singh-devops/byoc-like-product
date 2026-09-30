"use client";

import { useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { CloudAccount, CloudProviderName, Network, NetworkDetails } from "@/lib/types";

import { REGIONS } from "./CloudAccountDialog";
import { Icon } from "./Icon";
import { Alert, Dialog, ErrorAlert, Field } from "./ui";

const EXAMPLES: Record<CloudProviderName, { vpc: string; subnets: string }> = {
  gcp: { vpc: "prod-vpc", subnets: "db-subnet" },
  aws: {
    vpc: "vpc-0a1b2c3d4e5f67890",
    subnets: "subnet-0a1b2c3d4e5f60000, subnet-0a1b2c3d4e5f60001, subnet-0a1b2c3d4e5f60002",
  },
};

export function CheckList({ checks }: { checks: NetworkDetails["checks"] }) {
  return (
    <ul className="check-list">
      {checks.map((check) => (
        <li key={check.key} data-status={check.status}>
          <Icon name={check.status === "passed" ? "checkCircle" : check.status === "failed" ? "alertOctagon" : "alertTriangle"} />
          <span>
            <strong>{check.name}</strong>
            <span className="muted"> · {check.message}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** What the cloud reported about a network: ranges, zones and the checks behind them. */
export function NetworkDetailsView({ details }: { details: NetworkDetails }) {
  return (
    <div className="stack" style={{ gap: 10 }}>
      {details.valid ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Subnet</th>
                <th>Range</th>
                <th>Zone</th>
                <th>Free addresses</th>
              </tr>
            </thead>
            <tbody>
              {details.subnets.map((subnet) => (
                <tr key={subnet.id}>
                  <td className="break">{subnet.name}</td>
                  <td>
                    <code>{subnet.cidr}</code>
                  </td>
                  <td>{subnet.zone ?? `Regional (${details.zones.join(", ")})`}</td>
                  <td>{subnet.available_ips.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <CheckList checks={details.checks} />
      {details.error ? (
        <Alert tone="error" title={details.error.message}>
          {details.error.suggested_action ?? details.error.reason}
        </Alert>
      ) : null}
    </div>
  );
}

export function NetworkDialog({
  environmentId,
  environmentName,
  provider,
  accounts,
  mock,
  onClose,
  onCreated,
}: {
  environmentId: string;
  environmentName: string;
  provider: CloudProviderName;
  accounts: CloudAccount[];
  mock: boolean;
  onClose: () => void;
  onCreated: (network: Network) => void;
}) {
  const usable = useMemo(
    () => accounts.filter((a) => a.provider === provider && a.status === "CONNECTED"),
    [accounts, provider],
  );
  const [accountId, setAccountId] = useState(usable[0]?.id ?? "");
  const account = usable.find((a) => a.id === accountId);
  const [name, setName] = useState("");
  const [region, setRegion] = useState(account?.region ?? REGIONS[provider][0]);
  const [vpc, setVpc] = useState("");
  const [subnets, setSubnets] = useState("");
  const [details, setDetails] = useState<NetworkDetails | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"lookup" | "register" | null>(null);
  const fields = error instanceof ApiError ? error.fields : {};
  const aws = provider === "aws";
  const subnetList = subnets
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const lookupBody = { cloud_account_id: accountId, region, vpc, subnets: subnetList };
  const complete = Boolean(accountId && region && vpc && subnetList.length);

  const edit = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setDetails(null);
  };

  const lookup = async () => {
    setBusy("lookup");
    setError(null);
    try {
      setDetails(await api<NetworkDetails>("/networks/lookup", { method: "POST", body: lookupBody }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const register = async () => {
    setBusy("register");
    setError(null);
    try {
      const network = await api<Network>(`/environments/${environmentId}/networks`, {
        method: "POST",
        body: { ...lookupBody, name },
      });
      onCreated(network);
    } catch (err) {
      setError(err);
      setBusy(null);
    }
  };

  return (
    <Dialog
      title={`Register a network in ${environmentName}`}
      onClose={onClose}
      footer={
        <>
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button" onClick={() => void lookup()} disabled={!complete || busy !== null}>
            <Icon name="refresh" size={14} /> {busy === "lookup" ? "Fetching..." : "Fetch details"}
          </button>
          <button
            className="button button-primary"
            onClick={() => void register()}
            disabled={!complete || !name || !details?.valid || busy !== null}
          >
            {busy === "register" ? "Registering..." : "Register network"}
          </button>
        </>
      }
    >
      <ErrorAlert error={error} />
      {usable.length === 0 ? (
        <Alert tone="warning" title={`No connected ${aws ? "AWS account" : "GCP project"}`}>
          Add and validate one under Cloud accounts first.
        </Alert>
      ) : (
        <div className="form">
          <p className="muted">
            Point the platform at a {aws ? "VPC and subnets" : "VPC network and subnet"} you already run. The platform
            reads their details from the cloud; it never creates or changes them. It only adds firewall rules that
            apply to the cluster&apos;s own VMs.
          </p>
          <div className="field-row">
            <Field label="Network name" htmlFor="network-name" error={fields.name} hint="How this network appears on the platform.">
              <input
                id="network-name"
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value.trim())}
                placeholder={aws ? "private-app-vpc" : "prod-network"}
              />
            </Field>
            <Field label={aws ? "AWS account" : "GCP project"} htmlFor="network-account" error={fields.cloud_account_id}>
              <select
                id="network-account"
                className="input"
                value={accountId}
                onChange={(e) => {
                  edit(setAccountId)(e.target.value);
                  const next = usable.find((a) => a.id === e.target.value);
                  if (next?.region) setRegion(next.region);
                }}
              >
                {usable.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.project_id})
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Region" htmlFor="network-region" error={fields.region}>
            <input
              id="network-region"
              className="input"
              list={`network-regions-${provider}`}
              value={region}
              onChange={(e) => edit(setRegion)(e.target.value.trim())}
            />
            <datalist id={`network-regions-${provider}`}>
              {REGIONS[provider].map((r) => (
                <option key={r} value={r} />
              ))}
            </datalist>
          </Field>
          <Field
            label={aws ? "VPC ID" : "VPC network name"}
            htmlFor="network-vpc"
            error={fields.vpc}
            hint={aws ? undefined : "A network in this project. Shared VPC is not supported yet."}
          >
            <input
              id="network-vpc"
              className="input"
              value={vpc}
              onChange={(e) => edit(setVpc)(e.target.value.trim())}
              placeholder={EXAMPLES[provider].vpc}
            />
          </Field>
          <Field
            label={aws ? "Subnet IDs" : "Subnet name"}
            htmlFor="network-subnets"
            error={fields.subnets}
            hint={
              aws
                ? "One per availability zone, separated by commas. High availability needs subnets in three zones."
                : "One regional subnet; it serves every zone of the region."
            }
          >
            <input
              id="network-subnets"
              className="input"
              value={subnets}
              onChange={(e) => edit(setSubnets)(e.target.value)}
              placeholder={EXAMPLES[provider].subnets}
            />
          </Field>
          {details ? <NetworkDetailsView details={details} /> : null}
          {mock ? (
            <Alert tone="info" title="Mock mode">
              The lookup is simulated.{" "}
              {aws ? (
                <>
                  IDs containing <code>dead</code> are not found, <code>0bad</code> belongs to another VPC,{" "}
                  <code>beef</code> has no NAT route; a subnet&apos;s zone follows its last digit (0 → a, 1 → b, 2 → c).
                </>
              ) : (
                <>
                  Names containing <code>notfound</code>, <code>othervpc</code>, <code>proxy</code>, <code>nonat</code>,{" "}
                  <code>nopga</code> or <code>small</code> show those problems.
                </>
              )}
            </Alert>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}
