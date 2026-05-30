import { Users } from "lucide-react";

import { ComingSoon } from "../_components/coming-soon";

export default function SettingsMembersPage() {
  return (
    <ComingSoon
      title="Members"
      Icon={Users}
      description="Invite teammates and manage their access to your workspace."
    />
  );
}
