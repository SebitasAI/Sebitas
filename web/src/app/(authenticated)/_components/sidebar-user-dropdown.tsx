"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useClerk, useUser } from "@clerk/nextjs";

// Custom user dropdown ported from Antiff's SidebarUserDropdown. Replaces
// Clerk's prebuilt <UserButton> so the popover matches the dark sidebar
// + ink-on-white visual language. Stripped of the organization switcher
// — Misterr's "workspace" is the Slack workspace (resolved from the
// session, not from Clerk Organizations), so the dropdown just shows
// the active workspace name as a non-interactive row. When/if Clerk
// Organizations get adopted, add switching back here.

export function SidebarUserDropdown({
  workspaceName,
}: {
  workspaceName?: string | null;
}) {
  const { user } = useUser();
  const clerk = useClerk();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(e: PointerEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const name =
    user?.fullName ??
    user?.firstName ??
    user?.primaryEmailAddress?.emailAddress?.split("@")[0] ??
    "Cuenta";
  const email = user?.primaryEmailAddress?.emailAddress ?? "";
  const imageUrl = user?.imageUrl ?? null;

  async function onSignOut() {
    setOpen(false);
    await clerk.signOut(() => router.push("/sign-in"));
  }

  return (
    <div ref={rootRef} className="relative w-full">
      <button
        type="button"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        className="group flex w-full min-w-0 items-center gap-2 rounded-lg p-2 text-left transition-colors hover:bg-white"
      >
        <Avatar imageUrl={imageUrl} fallback={name} size="sm" />
        <span className="truncate text-[13px] font-medium text-neutral-700 transition-colors group-hover:text-[var(--color-ink-deep)]">
          {name}
        </span>
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-[100] mb-2 w-[272px] overflow-hidden rounded-xl border border-[var(--color-border)] bg-white text-[var(--color-ink-deep)] shadow-[0_16px_40px_rgba(0,0,0,0.18)]"
        >
          <header className="flex items-center gap-3 px-4 py-3">
            <Avatar imageUrl={imageUrl} fallback={name} size="md" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{name}</div>
              {email ? (
                <div className="truncate text-[11px] text-neutral-500">
                  {email}
                </div>
              ) : null}
            </div>
          </header>

          {workspaceName ? (
            <>
              <div className="border-t border-[var(--color-border)]" />
              <div className="px-4 pt-3 pb-1.5 text-[10px] font-medium uppercase tracking-wide text-neutral-500">
                Workspace
              </div>
              <div className="px-2 pb-1.5">
                <div className="flex w-full items-center gap-2.5 rounded-lg bg-[var(--color-surface-fog)] px-2 py-1.5 text-sm">
                  <span className="inline-flex size-6 items-center justify-center rounded-md bg-[#FF5200]/15 text-[10px] font-bold text-[#FF5200]">
                    {workspaceName.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="flex-1 truncate">{workspaceName}</span>
                  <CheckIcon />
                </div>
              </div>
            </>
          ) : null}

          <div className="border-t border-[var(--color-border)]" />
          <ul className="py-1.5">
            <li>
              <Link
                href="/settings/account"
                onClick={() => setOpen(false)}
                className="flex w-full items-center gap-2.5 px-4 py-2 text-sm transition-colors hover:bg-[var(--color-surface-fog)]"
              >
                <UserIcon />
                <span>Cuenta</span>
              </Link>
            </li>
            <li>
              <button
                type="button"
                onClick={onSignOut}
                className="flex w-full items-center gap-2.5 px-4 py-2 text-left text-sm transition-colors hover:bg-[var(--color-surface-fog)]"
              >
                <SignOutIcon />
                <span>Cerrar sesión</span>
              </button>
            </li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Avatar({
  imageUrl,
  fallback,
  size,
}: {
  imageUrl: string | null;
  fallback: string;
  size: "sm" | "md";
}) {
  const sizeClass = size === "sm" ? "size-7" : "size-9";
  const textClass = size === "sm" ? "text-[10px]" : "text-xs";
  const initials = fallback
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  if (imageUrl) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={imageUrl}
        alt={fallback}
        className={`${sizeClass} shrink-0 rounded-md object-cover`}
      />
    );
  }
  return (
    <span
      className={`${sizeClass} ${textClass} inline-flex shrink-0 items-center justify-center rounded-md bg-[#FF5200]/15 font-bold text-[#FF5200]`}
    >
      {initials}
    </span>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="#FF5200" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="size-4">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
function UserIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4">
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="10" r="3" />
      <path d="M6.5 19a6 6 0 0 1 11 0" />
    </svg>
  );
}
function SignOutIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}
