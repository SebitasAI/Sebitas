"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  Building2,
  CalendarClock,
  CreditCard,
  Languages,
  ShieldCheck,
  UserCircle,
  Users,
  Zap,
} from "lucide-react";
import { useAuth } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";

import { adminApi } from "@/lib/api/admin";

import {
  HomeIcon,
  IntegrationsIcon,
  SkillsIcon,
} from "./nav-icons";
import { SidebarUserDropdown } from "./sidebar-user-dropdown";
import { OrganizationSwitcher } from "@clerk/nextjs";
import { WorkspaceSelector } from "./workspace-selector";

// Visual shell ported from Antiff's PlatformShell + PlatformSidebar.
// Three states:
//   - expanded:  sidebar visible, outer frame has 8px padding around the
//                white card, main content offset by sidebar width.
//   - collapsed: sidebar hidden, frame padding goes to zero so the white
//                card fills the viewport. A 12px hot zone on the left
//                edge triggers peek-on-hover.
//   - peek:      transient overlay while collapsed; sidebar slides in as
//                a floating panel (rounded + shadow). Leaving the panel
//                drops back to collapsed without persisting the change.
// Cmd/Ctrl + . toggles collapsed (matches Antiff's shortcut).
//
// Persistence: `misterr:sidebar:collapsed` in localStorage. Reads on
// mount, writes on every toggle. Guards against localStorage being
// blocked (private mode) by swallowing the read/write errors.

const COLLAPSE_KEY = "misterr:sidebar:collapsed";

type NavItem = {
  href: string;
  label: string;
  icon: React.ReactNode;
};

const MAIN_NAV: NavItem[] = [
  { href: "/dashboard", label: "Inicio", icon: <HomeIcon /> },
  { href: "/skills", label: "Skills", icon: <SkillsIcon /> },
  { href: "/integrations", label: "Integraciones", icon: <IntegrationsIcon /> },
  // Outline icon (lucide) here is intentional: the filled SVGs in
  // ./nav-icons were ported from Antiff and don't have a calendar variant.
  // strokeWidth tuned to read at the same visual weight as the filled set.
  { href: "/scheduled-tasks", label: "Scheduled tasks", icon: <CalendarClock className="size-5" strokeWidth={1.75} /> },
  { href: "/automations", label: "Automations", icon: <Zap className="size-5" strokeWidth={1.75} /> },
];

const SETTINGS_NAV: NavItem[] = [
  { href: "/settings/account", label: "Account", icon: <UserCircle className="size-5" strokeWidth={1.75} /> },
  { href: "/settings/workspace", label: "Workspace", icon: <Building2 className="size-5" strokeWidth={1.75} /> },
  { href: "/settings/members", label: "Members", icon: <Users className="size-5" strokeWidth={1.75} /> },
  { href: "/settings/billing", label: "Billing", icon: <CreditCard className="size-5" strokeWidth={1.75} /> },
  { href: "/settings/preferences", label: "Preferences", icon: <Languages className="size-5" strokeWidth={1.75} /> },
];

function useCollapsedSidebar(): readonly [boolean, (next: boolean) => void] {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(COLLAPSE_KEY);
      // The setState-in-effect lint rule fires here, but this is the
      // standard SSR-safe pattern for hydrating from localStorage: server
      // renders with the default (false) so HTML matches, then client
      // post-hydration upgrades to the persisted value. A lazy initializer
      // would cause a hydration mismatch when the saved value differs from
      // the server-rendered default.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (saved === "true") setCollapsed(true);
    } catch {
      // private mode / disabled storage: ignore.
    }
  }, []);

  const update = useCallback((next: boolean) => {
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSE_KEY, String(next));
    } catch {
      // ignore.
    }
  }, []);

  return [collapsed, update] as const;
}

export function DashboardShell({
  children,
}: {
  children: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useCollapsedSidebar();
  const [peeking, setPeeking] = useState(false);
  // Admin-status probe. Tiny query (cached forever in the session). When
  // `is_admin` we splice an "Admin" entry into MAIN_NAV; non-admins never
  // see the link. The /admin page itself enforces backend-side too.
  const { getToken } = useAuth();
  const adminQuery = useQuery({
    queryKey: ["admin", "me"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) return { is_admin: false, email: null };
      try {
        return await adminApi.me(token);
      } catch {
        return { is_admin: false, email: null };
      }
    },
    staleTime: 5 * 60 * 1000,
  });

  // Cmd/Ctrl + . global toggle.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ".") {
        e.preventDefault();
        setCollapsed(!collapsed);
        setPeeking(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [collapsed, setCollapsed]);

  const sidebarVisible = !collapsed || peeking;
  const frameVisible = !collapsed;
  const isPeek = collapsed && peeking;

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#FAFAFA] text-neutral-600">
      <div
        className={`min-h-0 flex-1 transition-[padding] duration-300 ease-out ${frameVisible ? "py-2 pr-2" : "p-0"}`}
      >
        <div className="relative h-full w-full overflow-hidden">
          {collapsed ? (
            <div
              aria-hidden
              onMouseEnter={() => setPeeking(true)}
              className="absolute left-0 top-0 z-20 hidden h-full w-3 md:block"
            />
          ) : null}

          <aside
            onMouseLeave={isPeek ? () => setPeeking(false) : undefined}
            className={[
              "absolute z-30 hidden w-[256px] flex-col bg-[#FAFAFA] text-neutral-600 md:flex",
              // Border lives on the main card (border-l) so it follows the
              // card's rounded corners. A sidebar border-r would intersect
              // the rounded top-left and produce a visible double line.
              // When peeking we DO add a border-r because the sidebar then
              // floats as its own rounded panel with no card touching it.
              "transition-[transform,top,bottom,left,border-radius,box-shadow] duration-300 ease-out",
              isPeek
                ? "left-2 top-2 bottom-2 rounded-lg border border-[var(--color-border)] shadow-[0_8px_30px_rgba(0,0,0,0.18)]"
                : "left-0 top-0 bottom-0 rounded-none",
              sidebarVisible ? "translate-x-0" : "-translate-x-full",
            ].join(" ")}
          >
            <SidebarContent
              collapsed={collapsed}
              isAdmin={adminQuery.data?.is_admin ?? false}
              onToggleCollapse={() => {
                setCollapsed(!collapsed);
                setPeeking(false);
              }}
            />
          </aside>

          <main
            className={[
              "h-full overflow-y-auto bg-white text-[var(--color-ink-deep)]",
              "transition-[margin-left,border-radius] duration-300 ease-out",
              // When expanded: rounded card with top/right/bottom border
              // outlining the white card against the light gray frame
              // (the sidebar's right border is the left edge). When
              // collapsed: the card takes the full viewport, no rounding,
              // no extra borders (the chrome would look like a stray
              // outline against the edge of the window).
              collapsed
                ? "ml-0 rounded-none"
                : "rounded-lg border border-[var(--color-border)] md:ml-[256px]",
            ].join(" ")}
          >
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}

function SidebarContent({
  collapsed,
  isAdmin,
  onToggleCollapse,
}: {
  collapsed: boolean;
  isAdmin: boolean;
  onToggleCollapse: () => void;
}) {
  const pathname = usePathname();
  const isSettings = pathname.startsWith("/settings");
  // Splice the Admin link into MAIN_NAV when the calling user is a
  // platform admin. The /admin page itself enforces server-side too;
  // hiding the link is just to keep the sidebar relevant for everyone else.
  const nav = isSettings
    ? SETTINGS_NAV
    : isAdmin
      ? [
          ...MAIN_NAV,
          {
            href: "/admin",
            label: "Admin",
            icon: <ShieldCheck className="size-5" strokeWidth={1.75} />,
          },
        ]
      : MAIN_NAV;

  return (
    <div className="flex h-full flex-col px-3 pt-5 pb-3">
      <div className="flex items-center justify-between gap-3 pb-5">
        <Link
          href="/dashboard"
          aria-label="Misterr"
          className="inline-flex pl-2"
        >
          <Image
            src="/misterr-logo.svg"
            alt="Misterr"
            width={110}
            height={28}
            priority
          />
        </Link>
        <SidebarToggleButton
          onClick={onToggleCollapse}
          collapsed={collapsed}
        />
      </div>

      {/* Org switcher sits between the logo and the nav, only on
          non-settings pages. In settings the sub-nav owns this area.
          We use Clerk's OrganizationSwitcher (slice T-5) as the source
          of truth for the active workspace; the legacy custom
          WorkspaceSelector is kept as a fallback for users whose JWT
          template hasn't been updated yet (no `org_id` claim) but is
          rendered only when Clerk has no orgs to show. */}
      {!isSettings ? (
        <div className="pb-3">
          <OrganizationSwitcher
            hidePersonal
            afterCreateOrganizationUrl="/dashboard"
            afterSelectOrganizationUrl="/dashboard"
            appearance={{
              elements: {
                organizationSwitcherTrigger:
                  "w-full justify-between rounded-lg border border-[var(--color-border)] bg-white px-2.5 py-1.5 text-[13px] text-[var(--color-ink-deep)] hover:bg-[var(--color-surface-fog)]",
                organizationPreviewMainIdentifier: "text-[13px] font-medium",
              },
            }}
          />
          <noscript>
            <WorkspaceSelector />
          </noscript>
        </div>
      ) : null}

      {isSettings ? (
        <Link
          href="/dashboard"
          className="mb-2 inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] text-neutral-500 transition-colors hover:text-[var(--color-ink-deep)]"
        >
          <ChevronLeft />
          <span>Back to dashboard</span>
        </Link>
      ) : null}

      <nav className="flex flex-1 flex-col gap-0.5">
        {nav.map((item) => {
          const active =
            item.href === pathname ||
            (item.href !== "/dashboard" && pathname.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={[
                "group flex items-center gap-2 rounded-lg p-2 text-sm font-medium transition-colors",
                active
                  ? "bg-white text-[var(--color-ink-deep)] shadow-[0_0_0_1px_rgba(0,0,0,0.06),0_1px_2px_rgba(0,0,0,0.04)]"
                  : "text-neutral-600 hover:bg-white hover:text-[var(--color-ink-deep)]",
              ].join(" ")}
            >
              <span
                className={[
                  "size-5 shrink-0 transition-colors",
                  active ? "text-[#FF5200]" : "text-current",
                ].join(" ")}
              >
                {item.icon}
              </span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-2">
        <SidebarUserDropdown />
      </div>
    </div>
  );
}

// Three-layer animated toggle button, ported from Antiff's SidebarToggleButton.
// At rest shows a rectangle ("page"). On hover shows an arrow swap effect:
// the leftward portion slides out while the action arrow slides in. Tooltip
// surfaces "Hide sidebar (⌘+.)" / "Show sidebar (⌘+.)" so the keyboard
// shortcut is discoverable.
function SidebarToggleButton({
  onClick,
  collapsed,
}: {
  onClick: () => void;
  collapsed: boolean;
}) {
  const label = collapsed ? "Show sidebar" : "Hide sidebar";
  return (
    <div className="group/tip relative">
      <button
        type="button"
        onClick={onClick}
        aria-label={label}
        className="group inline-flex size-7 items-center justify-center rounded-lg text-neutral-300 transition-colors hover:bg-black/[0.04] hover:text-neutral-400"
      >
        <span className="relative size-5 overflow-hidden">
          {/* Base rectangle ("page"). Always rendered. */}
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="none"
            className="absolute inset-0 size-full"
          >
            <path
              fill="currentColor"
              d="M15.25 3H4.75A2.752 2.752 0 0 0 2 5.75v8.5A2.752 2.752 0 0 0 4.75 17h10.5A2.752 2.752 0 0 0 18 14.25v-8.5A2.752 2.752 0 0 0 15.25 3Z"
            />
          </svg>
          {/* Inner left bar — slides out on hover. */}
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="none"
            className="absolute inset-0 size-full transition duration-200 ease-out group-hover:-translate-x-full group-hover:opacity-0"
          >
            <path
              fill="#525252"
              d="M7.19055 13.2C7.19055 13.6418 6.83304 14 6.39203 14C5.95102 14 5.5935 13.6418 5.5935 13.2L5.59351 6.8C5.59351 6.35817 5.95102 6 6.39203 6C6.83304 6 7.19055 6.35817 7.19055 6.8L7.19055 13.2Z"
            />
          </svg>
          {/* Arrow — slides in on hover. */}
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="none"
            className="absolute inset-0 size-full translate-x-full opacity-0 transition duration-200 ease-out group-hover:translate-x-0 group-hover:opacity-100"
          >
            <path
              fill="#525252"
              d="M9.29883 6.64118C9.59173 6.34841 10.0665 6.34833 10.3594 6.64118C10.6521 6.93403 10.6521 7.40885 10.3594 7.70172L8.81152 9.24957H13C13.4141 9.24957 13.7498 9.58551 13.75 9.99957C13.75 10.4138 13.4142 10.7496 13 10.7496H8.81152L10.3594 12.2984C10.652 12.5913 10.6522 13.0661 10.3594 13.3589C10.0666 13.6517 9.59172 13.6515 9.29883 13.3589L6.46973 10.5298C6.4333 10.4934 6.40138 10.4541 6.37402 10.4127L6.30957 10.2916C6.30877 10.2897 6.3084 10.2876 6.30762 10.2857C6.30017 10.2676 6.29602 10.2485 6.29004 10.23C6.26647 10.1572 6.25 10.0802 6.25 9.99957C6.25004 9.9188 6.26637 9.842 6.29004 9.76911C6.29558 9.75199 6.29886 9.73411 6.30566 9.71735L6.30957 9.70758C6.31882 9.68575 6.33157 9.6658 6.34277 9.64508C6.37653 9.58257 6.41692 9.52212 6.46973 9.4693L9.29883 6.64118Z"
            />
          </svg>
        </span>
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 whitespace-nowrap rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] opacity-0 shadow-[0_8px_24px_rgba(0,0,0,0.18)] transition-opacity duration-150 group-hover/tip:opacity-100"
      >
        {label} (⌘+.)
      </span>
    </div>
  );
}

function ChevronLeft() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}
