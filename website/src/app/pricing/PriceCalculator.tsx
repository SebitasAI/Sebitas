"use client";

import { useState } from "react";

// Pricing math derives from the same source-of-truth as the runtime billing
// system (see app/billing/plans.py). 1 credit = $0.001 USD sales price; 5x
// markup on real LLM cost lands at 80% gross margin.
//
// Slider range covers Free floor (50,000 credits = $50 sales value, given
// away) all the way to Business ceiling (10M credits = $10,000/mo). Anything
// above that is Enterprise (contract, no slider).

// ── Pricing model (tweak here, in one place) ────────────────────────────────
const CONFIG = {
  costPerCredit: 0.001, // USD per credit ($1.00 per 1,000 credits)
  ratePer1000: 1.0, // USD per 1,000 credits (display)
  included: {
    usd: 50, // value of the perpetual Free tier / mo
    credits: 50000, // Free tier credits / mo (matches FREE_TIER_CREDITS_PER_MONTH)
  },
  slider: {
    min: 50000, // = Free tier ceiling
    max: 10000000, // = Business tier ceiling
    step: 50000,
    initial: 200000, // ~= mid-Starter, the most likely "I'm exploring" landing
  },
  defaultCreditsPerTask: 50, // placeholder, sets the margin, not the rate
  salesHref: "mailto:sales@misterr.ai?subject=Misterr%20Enterprise",
};

// en-US thousands separator, always integers.
const nf = new Intl.NumberFormat("en-US");
const fmt = (n: number) => nf.format(Math.round(n));

export default function PriceCalculator() {
  const [credits, setCredits] = useState(CONFIG.slider.initial);
  const [creditsPerTask, setCreditsPerTask] = useState(
    CONFIG.defaultCreditsPerTask,
  );

  const cost = Math.round(credits * CONFIG.costPerCredit);
  const atMax = credits >= CONFIG.slider.max;
  const tasks =
    creditsPerTask > 0 ? Math.round(credits / creditsPerTask) : null;

  // thumb position (0–100%) used to paint the track fill
  const pct =
    ((credits - CONFIG.slider.min) /
      (CONFIG.slider.max - CONFIG.slider.min)) *
    100;

  return (
    <div className="flex w-full max-w-[720px] flex-col gap-[32px] rounded-[20px] border border-[#191919] bg-white p-[28px] shadow-[0px_4px_0px_0px_#626262] sm:p-[40px]">
      {/* Top badge */}
      <div className="flex justify-center">
        <span className="rounded-full bg-[#faf5f1] px-[18px] py-[8px] text-center font-[family-name:var(--font-inter)] text-[14px] font-medium tracking-[-0.2px] text-[#626262] sm:text-[15px]">
          Included in your plan:{" "}
          <span className="font-semibold text-[#191919]">
            ${fmt(CONFIG.included.usd)} = {fmt(CONFIG.included.credits)} credits
          </span>{" "}
          / mo
        </span>
      </div>

      {/* Slider */}
      <div className="flex w-full flex-col gap-[14px]">
        <input
          type="range"
          min={CONFIG.slider.min}
          max={CONFIG.slider.max}
          step={CONFIG.slider.step}
          value={credits}
          onChange={(e) => setCredits(Number(e.target.value))}
          aria-label="Credits per month"
          className="price-slider h-[8px] w-full cursor-pointer appearance-none rounded-full outline-none"
          style={{
            background: `linear-gradient(90deg, #ff5200 0%, #ff5200 ${pct}%, #ece7e1 ${pct}%, #ece7e1 100%)`,
          }}
        />
        <div className="flex justify-between font-[family-name:var(--font-inter)] text-[12px] text-[#9a9a9a]">
          <span>{fmt(CONFIG.slider.min)}</span>
          <span>{fmt(CONFIG.slider.max)}</span>
        </div>
      </div>

      {/* Readouts: metric cards, or Enterprise message at the top of the range */}
      {atMax ? (
        <div className="flex flex-col items-center gap-[10px] rounded-[14px] border border-[#ff5200] bg-[#fff3ec] px-[24px] py-[28px] text-center">
          <p className="font-[family-name:var(--font-lexend)] text-[22px] font-semibold tracking-[-0.6px] text-[#191919]">
            Need more volume?
          </p>
          <a
            href={CONFIG.salesHref}
            className="font-[family-name:var(--font-inter)] text-[18px] font-semibold text-[#ff5200] underline-offset-4 hover:underline"
          >
            Talk to sales →
          </a>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
          <MetricCard label="Credits / mo" value={fmt(credits)} />
          <MetricCard label="Cost / mo" value={`$${fmt(cost)}`} accent />
        </div>
      )}

      {/* Value translator */}
      <div className="flex flex-col gap-[10px] border-t border-[#ece7e1] pt-[28px]">
        <div className="flex flex-wrap items-center gap-x-[8px] gap-y-[8px] font-[family-name:var(--font-inter)] text-[16px] text-[#191919] sm:text-[18px]">
          <span>Let&apos;s say: 1 task =</span>
          <input
            type="number"
            min={1}
            step={1}
            value={creditsPerTask}
            onChange={(e) => setCreditsPerTask(Math.max(0, Number(e.target.value)))}
            aria-label="Credits per task"
            className="w-[88px] rounded-[10px] border border-[#d9d4cd] bg-white px-[12px] py-[6px] text-center font-semibold text-[#191919] outline-none focus:border-[#ff5200]"
          />
          <span>credits</span>
          <span className="text-[#9a9a9a]">→</span>
          <span className="font-semibold text-[#191919]">
            ≈ {tasks !== null ? fmt(tasks) : "n/a"} tasks / mo
          </span>
        </div>
        <p className="font-[family-name:var(--font-inter)] text-[13px] leading-[1.5] text-[#9a9a9a]">
          Define this number. It&apos;s what sets your margin, not the per-credit
          rate.
        </p>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="flex flex-col gap-[6px] rounded-[14px] border border-[#ece7e1] bg-[#faf5f1] px-[24px] py-[22px]">
      <span className="font-[family-name:var(--font-inter)] text-[14px] font-medium text-[#626262]">
        {label}
      </span>
      <span
        className={`font-[family-name:var(--font-lexend)] text-[40px] font-semibold leading-[1.05] tracking-[-1.6px] ${
          accent ? "text-[#ff5200]" : "text-[#191919]"
        }`}
      >
        {value}
      </span>
    </div>
  );
}
