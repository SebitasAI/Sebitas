import type { LucideIcon } from "lucide-react";

import { PageBody, PageHeader } from "../../_components/page-header";

// Placeholder for settings tabs that exist in the sidebar but don't have
// real pages yet (Workspace, Members, Billing, Preferences). Keeps the
// nav navigable without 404s; each gets a real page in a follow-up slice.

export function ComingSoon({
  title,
  Icon,
  description,
}: {
  title: string;
  Icon: LucideIcon;
  description: string;
}) {
  return (
    <>
      <PageHeader
        title={title}
        Icon={({ className }) => <Icon className={className} strokeWidth={1.75} />}
      />
      <PageBody>
        <p className="text-xs text-neutral-500">{description}</p>
        <div className="mt-5 rounded-xl border border-dashed border-[var(--color-border)] bg-white p-8 text-center">
          <p className="text-sm font-medium text-[var(--color-ink-deep)]">
            Próximamente
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            Esta sección está en construcción.
          </p>
        </div>
      </PageBody>
    </>
  );
}
