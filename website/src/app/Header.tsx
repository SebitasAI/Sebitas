"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

// Dropdown contents — swap hrefs for real routes when they exist.
const MENUS: Record<string, { label: string; href: string }[]> = {
  Solutions: [
    { label: "Customer Support", href: "#" },
    { label: "Engineering", href: "#" },
    { label: "Marketing & Growth", href: "#" },
    { label: "Operations", href: "#" },
    { label: "Sales", href: "#" },
  ],
  Company: [
    { label: "About", href: "#" },
    { label: "Careers", href: "#" },
    { label: "Blog", href: "#" },
    { label: "Contact", href: "#" },
  ],
};

// CTA targets — placeholders; point to real app/contact URLs when ready.
const TALK_TO_SALES = "mailto:sales@misterr.ai";
const LOGIN_HREF = "/login";
const GET_STARTED_HREF = "/signup";

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
      <div className="mx-auto flex w-full max-w-[1280px] items-center justify-between px-[64px] py-[12px] backdrop-blur-[3px]">
        <Link href="/" className="relative h-[39px] shrink-0">
          <div className="flex size-full items-center px-[12px]">
            <img
              className="block h-[28px] w-[131px] max-w-none"
              src="/landing/misterr-logo.svg"
              alt="Misterr"
            />
          </div>
        </Link>

        <nav
          ref={navRef}
          className="flex items-center gap-[2px] rounded-[14px] border border-[#ececec] bg-white px-[6px] py-[4px] shadow-[0px_2px_8px_rgba(0,0,0,0.06)] shrink-0"
        >
          <Link
            href="#pricing"
            className="flex h-[39px] items-center rounded-[12px] px-[16px] hover:bg-[#f3f3f3]"
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
                  className="flex h-[39px] items-center gap-[3.99px] rounded-[12px] px-[16px] hover:bg-[#f3f3f3]"
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
                        className="block rounded-[8px] px-[12px] py-[9px] font-[family-name:var(--font-inter)] text-[15px] text-[#4a4a4a] hover:bg-[#f3f3f3]"
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
            href="#security"
            className="flex h-[39px] items-center rounded-[12px] px-[16px] hover:bg-[#f3f3f3]"
          >
            <p className={linkText}>Security</p>
          </Link>
        </nav>

        <div className="flex h-[39px] items-center gap-[8px] shrink-0">
          <a
            href={TALK_TO_SALES}
            className="flex h-full flex-col items-start justify-center rounded-[12px] bg-[#eee] pb-[4px] transition-transform active:translate-y-[2px]"
          >
            <div className="flex flex-[1_0_0] min-h-px min-w-[74px] items-center justify-center rounded-[12px] bg-white px-[17px]">
              <p className="font-[family-name:var(--font-geist)] text-[16px] font-medium leading-[19.2px] tracking-[-0.24px] text-[#191919] whitespace-nowrap">
                Talk to sales
              </p>
            </div>
          </a>
          <Link
            href={LOGIN_HREF}
            className="flex h-full flex-col items-start justify-center rounded-[12px] bg-[#626262] pb-[4px] transition-transform active:translate-y-[2px]"
          >
            <div className="flex flex-[1_0_0] min-h-px min-w-[74px] items-center justify-center rounded-[12px] bg-[#191919] px-[17px]">
              <p className="font-[family-name:var(--font-geist)] text-[16px] font-medium leading-[19.2px] tracking-[-0.24px] text-white whitespace-nowrap">
                Login
              </p>
            </div>
          </Link>
          <Link
            href={GET_STARTED_HREF}
            className="flex h-full flex-col items-start justify-center rounded-[12px] bg-[#cc4a00] pb-[4px] transition-transform active:translate-y-[2px]"
          >
            <div className="flex flex-[1_0_0] min-h-px min-w-[119px] items-center justify-center rounded-[12px] bg-[#ff5200] px-[17.58px]">
              <p className="font-[family-name:var(--font-geist)] text-[16px] font-medium leading-[19.2px] tracking-[-0.24px] text-white whitespace-nowrap">
                Get started
              </p>
            </div>
          </Link>
        </div>
      </div>
    </header>
  );
}
