import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

export const metadata: Metadata = {
  title: "Misterr",
  description: "Misterr — AI coworker dashboard.",
};

// ClerkProvider wraps the whole tree so any client component can call useUser
// / useAuth without per-page setup. Server components use `auth()` /
// `currentUser()` directly from @clerk/nextjs/server, which read the cookie
// the middleware seeded.
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body>{children}</body>
      </html>
    </ClerkProvider>
  );
}
