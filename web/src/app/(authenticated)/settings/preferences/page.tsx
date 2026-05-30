import { Languages } from "lucide-react";

import { ComingSoon } from "../_components/coming-soon";

export default function SettingsPreferencesPage() {
  return (
    <ComingSoon
      title="Preferences"
      Icon={Languages}
      description="Customize language, notifications, and other personal preferences."
    />
  );
}
