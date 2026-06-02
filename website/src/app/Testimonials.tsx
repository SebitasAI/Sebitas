"use client";

import { useEffect, useState } from "react";

// TODO testimonials: every quote must be REAL, on-product (Misterr the Slack coworker, NOT the
// disputes/chargeback product), and used with the customer's permission, including real name,
// role, and company. Fabricated or placeholder testimonials must never go live (FTC prohibits
// fake endorsements). If there are no real, permissioned quotes yet, hide this section entirely.

type Testimonial = {
  quote: string;
  name: string;
  role: string;
  company: string;
  avatar?: string;
};

// Obvious placeholders so no fake quote ships by accident. Replace with real,
// permissioned, on-product quotes (or hide the section) before launch.
const TESTIMONIALS: Testimonial[] = [
  {
    quote:
      "[Real customer quote about Misterr, in their own words, on-product]",
    name: "[Full name]",
    role: "[Role]",
    company: "[Company]",
  },
  {
    quote:
      "[Another real, specific quote about what Misterr shipped for the team]",
    name: "[Full name]",
    role: "[Role]",
    company: "[Company]",
  },
  {
    quote: "[A third permissioned quote from a happy Misterr customer]",
    name: "[Full name]",
    role: "[Role]",
    company: "[Company]",
  },
];

const AUTO_MS = 6500;

export default function Testimonials() {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const n = TESTIMONIALS.length;

  const go = (i: number) => setIndex(((i % n) + n) % n);

  // Gentle auto-advance; paused on hover/focus and for reduced-motion users.
  useEffect(() => {
    if (paused) return;
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const id = setInterval(() => setIndex((p) => (p + 1) % n), AUTO_MS);
    return () => clearInterval(id);
  }, [paused, n]);

  const t = TESTIMONIALS[index];

  return (
    <div
      className="flex w-full max-w-[820px] flex-col items-center gap-[28px]"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      aria-roledescription="carousel"
      aria-label="Customer testimonials"
    >
      {/* quote (min-height reserved so length changes don't shift layout) */}
      <div
        className="flex min-h-[260px] w-full items-center justify-center sm:min-h-[220px]"
        aria-live="polite"
      >
        <blockquote
          key={index}
          className="uc-fade text-center font-[family-name:var(--font-lexend)] text-[24px] font-medium leading-[1.4] tracking-[-0.8px] text-[#191919] sm:text-[32px] sm:tracking-[-1.2px] md:text-[38px]"
        >
          &ldquo;{t.quote}&rdquo;
        </blockquote>
      </div>

      {/* attribution */}
      <div className="flex items-center gap-[14px]">
        {t.avatar ? (
          <img
            src={t.avatar}
            alt={t.name}
            className="size-[48px] shrink-0 rounded-full object-cover"
          />
        ) : (
          <span className="size-[48px] shrink-0 rounded-full border border-[#cdb9a6] bg-[#e7d9c9]" />
        )}
        <div className="flex flex-col text-left">
          <span className="font-[family-name:var(--font-inter)] text-[16px] font-semibold tracking-[-0.3px] text-[#191919]">
            {t.name}
          </span>
          <span className="font-[family-name:var(--font-inter)] text-[14px] font-medium text-[#626262]">
            {t.role}, {t.company}
          </span>
        </div>
      </div>

      {/* controls */}
      <div className="flex items-center gap-[20px]">
        <button
          type="button"
          onClick={() => go(index - 1)}
          aria-label="Previous testimonial"
          className="flex size-[44px] items-center justify-center rounded-full border border-[#191919] bg-white drop-shadow-[0px_4px_0px_#626262] transition active:translate-y-[2px] active:drop-shadow-none"
        >
          <div className="rotate-180">
            <img className="size-[16px]" src="/landing/arrow-1.svg" alt="" />
          </div>
        </button>

        <div className="flex items-center gap-[8px]">
          {TESTIMONIALS.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => go(i)}
              aria-label={`Go to testimonial ${i + 1}`}
              aria-current={i === index}
              className={`h-[8px] rounded-full transition-all ${
                i === index ? "w-[22px] bg-[#191919]" : "w-[8px] bg-[#bcae9e]"
              }`}
            />
          ))}
        </div>

        <button
          type="button"
          onClick={() => go(index + 1)}
          aria-label="Next testimonial"
          className="flex size-[44px] items-center justify-center rounded-full border border-[#191919] bg-white drop-shadow-[0px_4px_0px_#626262] transition active:translate-y-[2px] active:drop-shadow-none"
        >
          <img className="size-[16px]" src="/landing/arrow-2.svg" alt="" />
        </button>
      </div>
    </div>
  );
}
