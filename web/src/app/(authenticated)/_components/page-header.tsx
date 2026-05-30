import type { ComponentType } from "react";

type IconComponent = ComponentType<{ className?: string }>;

interface PageHeaderProps {
  title: string;
  Icon: IconComponent;
  actions?: React.ReactNode;
}

/**
 * Reusable page header chrome. Ported from Antiff's PageHeader so any
 * page inside the dashboard gets the same icon-in-rounded-tile + title
 * + optional right-side actions layout. The icon tile uses the orange
 * brand color (#FF6A1A) on a soft tint, matching Antiff exactly.
 */
export function PageHeader({ title, Icon, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] bg-white px-6 py-4 md:px-12">
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className="inline-flex size-6 items-center justify-center rounded-md bg-[#FFE5D6] text-[#FF6A1A]"
        >
          <Icon className="size-4" />
        </span>
        <h1 className="text-base font-semibold text-[var(--color-ink-deep)]">
          {title}
        </h1>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function PageBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col px-6 py-5 md:px-12">
      {children}
    </div>
  );
}
