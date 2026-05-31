"use client";

import { useState } from "react";

// Placeholder testimonials: replace copy/authors with real ones.
const ITEMS = [
  { quote: "Misterr handles our disputes end to end. We barely touch them now.", name: "Ana Pérez", role: "COO, Retailco" },
  { quote: "It feels like a teammate that never sleeps. Output ships in Slack.", name: "Liam Carter", role: "Eng Lead, Buildr" },
  { quote: "Recovered revenue we'd written off. ROI was obvious in week one.", name: "Sofia Rossi", role: "Finance, Marketly" },
  { quote: "Triages bugs and opens PRs before I even read the alert.", name: "Noah Kim", role: "CTO, Stackly" },
  { quote: "Our support backlog dropped 60%. Customers feel the difference.", name: "Emma Stone", role: "Support, Helpr" },
];

const PER_VIEW = 3;
const CARD_W = 372;
const GAP = 24;

export default function Testimonials() {
  const [index, setIndex] = useState(0);
  const maxIndex = Math.max(0, ITEMS.length - PER_VIEW);
  const atStart = index === 0;
  const atEnd = index >= maxIndex;

  return (
    <>
      <div className="w-full max-w-[1164px] overflow-hidden">
        <div
          className="flex items-center gap-[24px] transition-transform duration-300 ease-out"
          style={{ transform: `translateX(-${index * (CARD_W + GAP)}px)` }}
        >
          {ITEMS.map((it, i) => (
            <div
              key={i}
              className="flex h-[351px] w-[372px] shrink-0 flex-col justify-between rounded-[12px] border border-[#191919] bg-white p-[28px] shadow-[0px_4px_0px_0px_#626262]"
            >
              <p className="font-[family-name:var(--font-inter)] text-[22px] font-medium leading-[1.35] tracking-[-0.5px] text-[#191919]">
                “{it.quote}”
              </p>
              <div>
                <p className="font-[family-name:var(--font-inter)] text-[18px] font-semibold text-[#191919]">
                  {it.name}
                </p>
                <p className="font-[family-name:var(--font-inter)] text-[15px] font-normal text-[#626262]">
                  {it.role}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-[24px] shrink-0">
        <button
          type="button"
          onClick={() => setIndex((i) => Math.max(0, i - 1))}
          disabled={atStart}
          aria-label="Previous testimonial"
          className="flex h-[40px] min-h-[40px] items-center justify-center rounded-[15984px] border border-[#191919] bg-white px-[25px] py-px drop-shadow-[0px_4px_0px_#626262] transition active:translate-y-[2px] active:drop-shadow-none disabled:cursor-not-allowed disabled:opacity-40"
        >
          <div className="rotate-180">
            <img className="size-[16px]" src="/landing/arrow-1.svg" alt="" />
          </div>
        </button>
        <button
          type="button"
          onClick={() => setIndex((i) => Math.min(maxIndex, i + 1))}
          disabled={atEnd}
          aria-label="Next testimonial"
          className="flex h-[40px] min-h-[40px] items-center justify-center rounded-[15984px] border border-[#191919] bg-white px-[25px] py-px drop-shadow-[0px_4px_0px_#626262] transition active:translate-y-[2px] active:drop-shadow-none disabled:cursor-not-allowed disabled:opacity-40"
        >
          <img className="size-[16px]" src="/landing/arrow-2.svg" alt="" />
        </button>
      </div>
    </>
  );
}
