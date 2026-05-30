import Image from "next/image";
import Link from "next/link";

// Misterr wordmark on the dark auth bg. The SVG is in /public so Next
// can serve it directly. Original viewBox is 155x39 — we render at a
// fixed display height of 39px to match Antiff's logo presence.

export function AuthLogo({ className = "" }: { className?: string }) {
  return (
    <Link
      href="/"
      aria-label="Misterr"
      className={`auth-rise inline-flex ${className}`}
    >
      <Image
        src="/misterr-logo.svg"
        alt="Misterr"
        width={155}
        height={39}
        priority
      />
    </Link>
  );
}
