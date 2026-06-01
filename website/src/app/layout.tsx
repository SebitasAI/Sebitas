import type { Metadata } from "next";
import { Lexend, Inter, Geist } from "next/font/google";
import "./globals.css";

const lexend = Lexend({
  subsets: ["latin"],
  variable: "--font-lexend",
  display: "swap",
});
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
});

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
    <html
      lang="en"
      className={`${lexend.variable} ${inter.variable} ${geist.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
