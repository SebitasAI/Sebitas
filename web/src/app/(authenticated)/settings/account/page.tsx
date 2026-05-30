import { UserCircle } from "lucide-react";

import { PageBody, PageHeader } from "../../_components/page-header";
import { AccountSection } from "./_components/account-section";

export default function SettingsAccountPage() {
  return (
    <>
      <PageHeader
        title="Account"
        Icon={({ className }) => (
          <UserCircle className={className} strokeWidth={1.75} />
        )}
      />
      <PageBody>
        <p className="text-xs text-neutral-500">
          Manage your personal profile, sign-in emails, and password.
        </p>
        <div className="mt-5 space-y-4">
          <AccountSection />
        </div>
      </PageBody>
    </>
  );
}
