"use client";

import { useState } from "react";
import { useClerk, useUser } from "@clerk/nextjs";
import {
  Check,
  Loader2,
  Mail,
  ShieldCheck,
  Trash2,
  UserCircle,
} from "lucide-react";

// Account settings sections. Ported visually from Antiff's AccountSection,
// adapted for Slack-only OAuth: the Password card is intentionally omitted
// because users without a password (OAuth-only) would see a card that
// they can't act on. Two-factor & sessions opens Clerk's hosted security
// page, which works regardless of auth strategy.

export function AccountSection() {
  const { user, isLoaded } = useUser();

  if (!isLoaded) {
    return <SectionSkeleton />;
  }
  if (!user) {
    return (
      <SettingsCard title="Account" Icon={UserCircle}>
        <p className="text-xs text-neutral-500">
          Sign in to manage your account.
        </p>
      </SettingsCard>
    );
  }

  return (
    <>
      <ProfileCard />
      <EmailsCard />
      <SecurityCard />
    </>
  );
}

// -----------------------------------------------------------------------------
// Profile (name + avatar)
// -----------------------------------------------------------------------------

function ProfileCard() {
  const { user } = useUser();
  const [editing, setEditing] = useState(false);
  const [firstName, setFirstName] = useState(user?.firstName ?? "");
  const [lastName, setLastName] = useState(user?.lastName ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) return null;

  async function onSave() {
    if (!user) return;
    setError(null);
    setSaving(true);
    try {
      await user.update({ firstName, lastName });
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsCard title="Profile" Icon={UserCircle}>
      <div className="flex flex-wrap items-center gap-4">
        <Avatar imageUrl={user.imageUrl ?? null} fallback={user.fullName ?? "?"} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {editing ? (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <LabeledInput
                label="First name"
                value={firstName}
                onChange={setFirstName}
                autoFocus
              />
              <LabeledInput
                label="Last name"
                value={lastName}
                onChange={setLastName}
              />
            </div>
          ) : (
            <>
              <div className="text-sm font-medium text-[var(--color-ink-deep)]">
                {user.fullName || user.firstName || "—"}
              </div>
              <div className="text-xs text-neutral-500">
                {user.primaryEmailAddress?.emailAddress ?? "No primary email"}
              </div>
            </>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {editing ? (
            <>
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setFirstName(user.firstName ?? "");
                  setLastName(user.lastName ?? "");
                  setError(null);
                }}
                className="rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] hover:bg-neutral-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onSave}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-ink-deep)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-60"
              >
                {saving ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : null}
                Save
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] hover:bg-neutral-50"
            >
              Edit
            </button>
          )}
        </div>
      </div>
      {error ? (
        <p className="mt-2 text-xs text-rose-700">{error}</p>
      ) : null}
    </SettingsCard>
  );
}

// -----------------------------------------------------------------------------
// Emails
// -----------------------------------------------------------------------------

function EmailsCard() {
  const { user } = useUser();
  const [adding, setAdding] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) return null;

  async function onAdd() {
    if (!user || !newEmail.trim()) return;
    setError(null);
    setWorking(true);
    try {
      await user.createEmailAddress({ email: newEmail.trim() });
      setNewEmail("");
      setAdding(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add email.");
    } finally {
      setWorking(false);
    }
  }

  async function onSetPrimary(emailId: string) {
    if (!user) return;
    try {
      await user.update({ primaryEmailAddressId: emailId });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set primary.");
    }
  }

  async function onRemove(emailId: string) {
    if (!user) return;
    const email = user.emailAddresses.find((e) => e.id === emailId);
    if (!email) return;
    try {
      await email.destroy();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove email.");
    }
  }

  return (
    <SettingsCard title="Email addresses" Icon={Mail}>
      <ul className="flex flex-col divide-y divide-[var(--color-border)]">
        {user.emailAddresses.map((email) => {
          const isPrimary = email.id === user.primaryEmailAddressId;
          return (
            <li
              key={email.id}
              className="flex items-center gap-3 py-2 first:pt-0 last:pb-0"
            >
              <span className="flex-1 truncate text-sm">
                {email.emailAddress}
              </span>
              {isPrimary ? (
                <span className="inline-flex rounded-md bg-[#FFE5D6] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#FF6A1A]">
                  Primary
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => onSetPrimary(email.id)}
                  className="text-xs text-neutral-500 hover:text-[var(--color-ink-deep)]"
                >
                  Make primary
                </button>
              )}
              {!isPrimary ? (
                <button
                  type="button"
                  onClick={() => onRemove(email.id)}
                  aria-label="Remove email"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-rose-600"
                >
                  <Trash2 className="size-3.5" />
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>

      <div className="mt-3">
        {adding ? (
          <div className="flex items-center gap-2">
            <input
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="you@example.com"
              autoFocus
              className="flex-1 rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm outline-none focus:border-[var(--color-ink-deep)]"
            />
            <button
              type="button"
              onClick={onAdd}
              disabled={working || !newEmail.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-ink-deep)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              {working ? <Loader2 className="size-3 animate-spin" /> : null}
              Add
            </button>
            <button
              type="button"
              onClick={() => {
                setAdding(false);
                setNewEmail("");
                setError(null);
              }}
              className="text-xs text-neutral-500 hover:text-[var(--color-ink-deep)]"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] hover:bg-neutral-50"
          >
            + Add email address
          </button>
        )}
      </div>
      {error ? (
        <p className="mt-2 text-xs text-rose-700">{error}</p>
      ) : null}
    </SettingsCard>
  );
}

// -----------------------------------------------------------------------------
// Security (two-factor + sessions)
// -----------------------------------------------------------------------------

function SecurityCard() {
  const { openUserProfile } = useClerk();
  return (
    <SettingsCard title="Two-factor & sessions" Icon={ShieldCheck}>
      <p className="text-xs text-neutral-500">
        Manage TOTP authenticator apps, backup codes, and review the devices
        signed in to your account.
      </p>
      <button
        type="button"
        onClick={() => openUserProfile()}
        className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] hover:bg-neutral-50"
      >
        Open security settings
      </button>
    </SettingsCard>
  );
}

// -----------------------------------------------------------------------------
// Shared primitives
// -----------------------------------------------------------------------------

function SettingsCard({
  title,
  Icon,
  children,
}: {
  title: string;
  Icon: React.FC<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-white p-4 shadow-[0_1px_0_rgba(0,0,0,0.02)]">
      <div className="flex items-center gap-2 pb-3">
        <div className="flex size-7 items-center justify-center rounded-lg bg-black/[0.04]">
          <Icon className="size-3.5" />
        </div>
        <h2 className="text-sm font-semibold text-[var(--color-ink-deep)]">
          {title}
        </h2>
      </div>
      <div>{children}</div>
    </section>
  );
}

function SectionSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-24 animate-pulse rounded-xl border border-[var(--color-border)] bg-white"
        />
      ))}
    </div>
  );
}

function Avatar({
  imageUrl,
  fallback,
}: {
  imageUrl: string | null;
  fallback: string;
}) {
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
        className="size-12 shrink-0 rounded-lg object-cover"
      />
    );
  }
  return (
    <span className="inline-flex size-12 shrink-0 items-center justify-center rounded-lg bg-[#FFE5D6] text-base font-bold text-[#FF6A1A]">
      {initials}
    </span>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  autoFocus,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  autoFocus?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoFocus={autoFocus}
        className="rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm outline-none focus:border-[var(--color-ink-deep)]"
      />
    </label>
  );
}
