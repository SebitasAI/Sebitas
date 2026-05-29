import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Misterr, AI coworker for Slack",
  description:
    "Misterr is an AI coworker that lives in your Slack. Coming soon.",
  // Placeholder favicon: next/metadata will look for `app/icon.png` if absent
  // here; we leave this empty until real branding lands.
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
