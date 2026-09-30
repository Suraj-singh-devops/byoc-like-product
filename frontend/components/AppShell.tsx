"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { roleLabel } from "@/lib/format";

import { Icon, type IconName } from "./Icon";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { href: "/environments", label: "Environments", icon: "layers" },
  { href: "/clusters", label: "Clusters", icon: "database" },
  { href: "/cloud-accounts", label: "Cloud accounts", icon: "cloud" },
  { href: "/operations", label: "Operations", icon: "activity" },
  { href: "/audit-logs", label: "Audit logs", icon: "list" },
  { href: "/members", label: "Members", icon: "users" },
];

function UserMenu() {
  const { me, logout, switchOrganization } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!me) return null;
  const others = me.organizations.filter((o) => o.organization_id !== me.organization.id);
  return (
    <div className="menu" ref={ref}>
      <button className="button button-ghost" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span style={{ textAlign: "right", lineHeight: 1.2 }}>
          <span style={{ display: "block" }}>{me.user.name}</span>
          <span className="small muted">
            {me.organization.name} · {roleLabel(me.role)}
          </span>
        </span>
        <Icon name="chevronDown" size={14} />
      </button>
      {open ? (
        <div className="menu-panel" role="menu">
          <div className="menu-section">
            Signed in as
            <div style={{ color: "var(--text)", fontWeight: 500 }}>{me.user.email}</div>
          </div>
          {others.length ? (
            <>
              <div className="menu-divider" />
              <div className="menu-section">Switch organization</div>
              {others.map((org) => (
                <button
                  key={org.organization_id}
                  role="menuitem"
                  className="menu-item"
                  onClick={async () => {
                    setOpen(false);
                    await switchOrganization(org.organization_id);
                    router.push("/dashboard");
                  }}
                >
                  <Icon name="users" />
                  {org.organization_name} <span className="pill">{roleLabel(org.role)}</span>
                </button>
              ))}
            </>
          ) : null}
          <div className="menu-divider" />
          <button
            role="menuitem"
            className="menu-item"
            onClick={async () => {
              await logout();
              router.replace("/login");
            }}
          >
            <Icon name="logout" />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { meta } = useAuth();
  const [open, setOpen] = useState(false);

  useEffect(() => setOpen(false), [pathname]);

  return (
    <div className="shell">
      <aside className="sidebar" data-open={open} aria-label="Main navigation">
        <Link href="/dashboard" className="brand">
          <span className="brand-mark">
            <Icon name="database" size={16} />
          </span>
          <span>
            BYOC Platform
            <small>Managed databases, your cloud</small>
          </span>
        </Link>
        <nav className="stack" style={{ gap: 2 }}>
          {NAV.map((item) => {
            const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link key={item.href} href={item.href} className="nav-link" aria-current={current ? "page" : undefined}>
                <Icon name={item.icon} />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          {meta?.mock_mode ? (
            <div className="mock-banner">
              <strong>
                <Icon name="flask" size={14} /> Mock mode
              </strong>
              GCP, AWS, Terraform and the VMs are simulated. No cloud resources are created.
            </div>
          ) : null}
          <div className="small muted" style={{ padding: "0 8px" }}>
            v{meta?.version ?? "-"} ·{" "}
            <a href="/api/docs" target="_blank" rel="noreferrer">
              API docs
            </a>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="icon-button menu-button" aria-label="Open navigation" onClick={() => setOpen(!open)}>
            <Icon name="menu" />
          </button>
          <UserMenu />
        </header>
        <main className="content" id="main">
          {children}
        </main>
      </div>
    </div>
  );
}
