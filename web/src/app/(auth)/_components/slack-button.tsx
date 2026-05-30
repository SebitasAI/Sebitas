"use client";

// "Continue with Slack" button. Mirrors Antiff's GoogleButton shape +
// styling but with the Slack mark instead of Google's. Single button
// because Misterr is Slack-only OAuth (no email/password fallback).

export function SlackButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex h-11 w-full items-center justify-center gap-2.5 rounded-lg border border-[var(--color-ink-deep)] bg-white px-4 text-sm font-medium text-[var(--color-ink-deep)] transition hover:bg-[var(--color-surface-fog)] disabled:cursor-not-allowed disabled:opacity-50"
    >
      <SlackMark className="size-[18px]" />
      <span>{children}</span>
    </button>
  );
}

function SlackMark({ className = "" }: { className?: string }) {
  // Official 4-color Slack mark; SVG copied from Slack brand kit.
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M6.5 15.5a2 2 0 1 1-2-2h2v2zm1 0a2 2 0 1 1 4 0v5a2 2 0 1 1-4 0v-5z"
        fill="#E01E5A"
      />
      <path
        d="M9.5 6.5a2 2 0 1 1-2-2v2h-0 2zm0 1a2 2 0 1 1 0 4h-5a2 2 0 1 1 0-4h5z"
        fill="#36C5F0"
      />
      <path
        d="M17.5 9.5a2 2 0 1 1 2-2h-2v2zm-1 0a2 2 0 1 1-4 0v-5a2 2 0 1 1 4 0v5z"
        fill="#2EB67D"
      />
      <path
        d="M14.5 17.5a2 2 0 1 1 2 2v-2h-2zm0-1a2 2 0 1 1 0-4h5a2 2 0 1 1 0 4h-5z"
        fill="#ECB22E"
      />
    </svg>
  );
}
