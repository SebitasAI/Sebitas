import { redirect } from "next/navigation";

// /settings on its own redirects to the first sub-tab. Matches Antiff:
// the settings root has no content of its own, it's a hub.
export default function SettingsRootPage() {
  redirect("/settings/account");
}
