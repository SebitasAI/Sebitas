import { CreditCard } from "lucide-react";

import { ComingSoon } from "../_components/coming-soon";

export default function SettingsBillingPage() {
  return (
    <ComingSoon
      title="Billing"
      Icon={CreditCard}
      description="View invoices, update your payment method, and manage your plan."
    />
  );
}
