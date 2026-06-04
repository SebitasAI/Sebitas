// Shared CTA button matching the Header's 3D-shadow style.
//
// The marketing site has three button "voices" and they should look
// identical across pages (Header, Hero, Pricing tiers, Solutions
// pages, Security page, closing CTAs). Inlining the 3D-shadow trick
// in every file made them drift. This component is the one place that
// owns the visual.
//
// Variants:
//   primary    — orange brand CTA (Get started, Empezar gratis,
//                Empezar Starter, Agendar llamada). Use for the
//                single action you most want the visitor to take.
//   secondary  — white face + gray 3D lip (Talk to sales,
//                support@misterr.ai, contact buttons). Same shape as
//                primary so a row of two sits visually balanced.
//   dark       — dark face + dark gray lip (Login, alternative
//                strong actions where orange is wrong).
//
// The 3D effect is structural (pb-[4px] on the outer + face div
// nested inside), not a CSS box-shadow. That way `active:translate-y-[2px]`
// produces the "press" animation Antiff's design uses, and the shadow
// itself remains crisp on every zoom level.

import Link from "next/link";
import type { ReactNode } from "react";

export type CtaVariant = "primary" | "secondary" | "dark";

const STYLES: Record<
  CtaVariant,
  { shadow: string; face: string; hover: string; text: string }
> = {
  primary: {
    shadow: "bg-[#cc4a00]",
    face: "bg-[#ff5200]",
    hover: "hover:bg-[#ff6a23]",
    text: "text-white",
  },
  secondary: {
    shadow: "bg-[#eee]",
    face: "bg-white",
    hover: "hover:bg-[#f1f1f1]",
    text: "text-[#191919]",
  },
  dark: {
    shadow: "bg-[#626262]",
    face: "bg-[#191919]",
    hover: "hover:bg-black",
    text: "text-white",
  },
};


export function CtaButton({
  href,
  variant = "primary",
  external,
  children,
  className = "",
}: {
  href: string;
  variant?: CtaVariant;
  // External: opens in a new tab. Defaults true when the href is an
  // absolute URL or a mailto: link.
  external?: boolean;
  children: ReactNode;
  // Optional wrapper class so the caller can size / align the button.
  // Inner structure (shadow, face, padding, font) is fixed for
  // consistency across the marketing site.
  className?: string;
}) {
  const v = STYLES[variant];
  const auto =
    href.startsWith("http://") ||
    href.startsWith("https://") ||
    href.startsWith("mailto:");
  const isExternal = external ?? auto;

  const inner = (
    <span
      className={`flex h-[48px] flex-col items-start justify-center rounded-[12px] ${v.shadow} pb-[4px] transition-transform active:translate-y-[2px]`}
    >
      <span
        className={`flex flex-[1_0_0] min-h-px min-w-[88px] items-center justify-center rounded-[12px] ${v.face} ${v.hover} px-[17px] transition-colors`}
      >
        <span
          className={`font-[family-name:var(--font-inter)] text-[15px] font-semibold leading-[19.2px] tracking-[-0.24px] ${v.text} whitespace-nowrap`}
        >
          {children}
        </span>
      </span>
    </span>
  );

  if (isExternal) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={`inline-flex ${className}`}
      >
        {inner}
      </a>
    );
  }
  return (
    <Link href={href} className={`inline-flex ${className}`}>
      {inner}
    </Link>
  );
}
