"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

// Dropdown contents. Solutions routes to dedicated `/solutions/<slug>`
// pages (one config + shared SolutionPage layout). Company items still
// point at `#` until those pages exist.
const MENUS: Record<string, { label: string; href: string }[]> = {
  Solutions: [
    { label: "Customer Support", href: "/solutions/customer-support" },
    { label: "Engineering", href: "/solutions/engineering" },
    { label: "Marketing & Growth", href: "/solutions/marketing" },
    { label: "Operations", href: "/solutions/operations" },
    { label: "Sales", href: "/solutions/sales" },
  ],
  Company: [
    { label: "About", href: "#" },
    { label: "Careers", href: "#" },
    { label: "Blog", href: "#" },
    { label: "Contact", href: "#" },
  ],
};

import {
  APP_SIGN_IN_URL,
  APP_SIGN_UP_URL,
  BOOK_SALES_URL,
} from "@/lib/app-url";

// CTA targets. See `src/lib/app-url.ts` for the single source of truth.
const TALK_TO_SALES = BOOK_SALES_URL;
const LOGIN_HREF = APP_SIGN_IN_URL;
const GET_STARTED_HREF = APP_SIGN_UP_URL;

const linkText =
  "font-[family-name:var(--font-inter)] text-[16px] font-normal leading-[19.2px] tracking-[-0.24px] text-[#4a4a4a] whitespace-nowrap";

export default function Header() {
  const [hidden, setHidden] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const navRef = useRef<HTMLElement | null>(null);

  // hide on scroll-down, show on scroll-up
  useEffect(() => {
    let last = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      if (y < 80) setHidden(false);
      else if (y > last + 4) {
        setHidden(true);
        setOpen(null);
      } else if (y < last - 4) setHidden(false);
      last = y;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // close dropdowns on outside click / Escape
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node))
        setOpen(null);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  return (
    <header
      className={`fixed left-0 top-0 z-50 w-full transition-transform duration-300 ease-out ${
        hidden ? "-translate-y-full" : "translate-y-0"
      }`}
    >
      <div className="mx-auto flex w-full max-w-[1280px] items-center justify-between px-[20px] py-[12px] backdrop-blur-[3px] md:px-[64px]">
        <Link href="/" className="relative h-[39px] shrink-0">
          <div className="flex size-full items-center px-[4px] md:px-[12px]">
            <img
              className="block h-[28px] w-[131px] max-w-none"
              src="/landing/misterr-logo.svg"
              alt="Misterr"
            />
          </div>
        </Link>

        <nav ref={navRef} className="hidden items-center gap-[2px] shrink-0 lg:flex">
          <Link
            href="/pricing"
            className="flex h-[39px] items-center rounded-[12px] px-[16px]"
          >
            <p className={linkText}>Pricing</p>
          </Link>

          {(["Solutions", "Company"] as const).map((name) => {
            const isOpen = open === name;
            return (
              <div key={name} className="relative">
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : name)}
                  aria-expanded={isOpen}
                  className="flex h-[39px] items-center gap-[3.99px] rounded-[12px] px-[16px]"
                >
                  <p className={linkText}>{name}</p>
                  <img
                    className={`size-[16px] transition-transform duration-200 ${
                      isOpen ? "rotate-180" : ""
                    }`}
                    src="/landing/chevron.svg"
                    alt=""
                  />
                </button>
                {isOpen && (
                  <div className="absolute left-0 top-[calc(100%+8px)] z-50 min-w-[210px] rounded-[12px] border border-[#ececec] bg-white p-[6px] shadow-[0px_8px_24px_rgba(0,0,0,0.12)]">
                    {MENUS[name].map((item) => (
                      <Link
                        key={item.label}
                        href={item.href}
                        onClick={() => setOpen(null)}
                        className="block rounded-[8px] px-[12px] py-[9px] font-[family-name:var(--font-inter)] text-[15px] text-[#4a4a4a]"
                      >
                        {item.label}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            );
          })}

          <Link
            href="/security"
            className="flex h-[39px] items-center rounded-[12px] px-[16px]"
          >
            <p className={linkText}>Security</p>
          </Link>
        </nav>

        <div className="flex h-[48px] items-center gap-[8px] shrink-0">
          <div className="hidden h-full sm:flex">
            <CtaButton
              href={TALK_TO_SALES}
              shadow="bg-[#eee]"
              face="bg-white"
              hover="hover:bg-[#f1f1f1]"
              text="text-[#191919]"
              external
            >
              Talk to sales
            </CtaButton>
          </div>
          <div className="hidden h-full md:flex">
            <CtaButton
              href={LOGIN_HREF}
              shadow="bg-[#626262]"
              face="bg-[#191919]"
              hover="hover:bg-black"
              text="text-white"
            >
              Login
            </CtaButton>
          </div>
          <CtaButton
            href={GET_STARTED_HREF}
            shadow="bg-[#cc4a00]"
            face="bg-[#ff5200]"
            hover="hover:bg-[#ff6a23]"
            text="text-white"
          >
            Get started
          </CtaButton>
        </div>
      </div>
    </header>
  );
}

function CtaButton({
  href,
  shadow,
  face,
  hover,
  text,
  children,
  external,
}: {
  href: string;
  shadow: string;
  face: string;
  hover: string;
  text: string;
  children: React.ReactNode;
  // When true, opens in a new tab with rel="noopener". Used by the Talk
  // to sales button so the booking widget pops up without losing the
  // user's place on the landing.
  external?: boolean;
}) {
  return (
    <a
      href={href}
      target={external ? "_blank" : undefined}
      rel={external ? "noopener noreferrer" : undefined}
      className={`flex h-full flex-col items-start justify-center rounded-[12px] ${shadow} pb-[4px] transition-transform active:translate-y-[2px]`}
    >
      <div
        className={`flex flex-[1_0_0] min-h-px min-w-[88px] items-center justify-center rounded-[12px] ${face} ${hover} px-[17px] transition-colors`}
      >
        <p
          className={`font-[family-name:var(--font-geist)] text-[16px] font-medium leading-[19.2px] tracking-[-0.24px] ${text} whitespace-nowrap`}
        >
          {children}
        </p>
      </div>
    </a>
  );
}
