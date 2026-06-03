"use client";

import { useState } from "react";

// Full FAQ content lives here (not in JSX). Some answers are still tied to
// unmade product decisions (see the // TODO notes); do not publish those as
// final until the underlying behavior/numbers are confirmed.
const FAQS: { q: string; a: string }[] = [
  {
    q: "What is a credit?",
    a: 'A credit is the unit we use to measure the work Misterr does for you. Different tasks use different amounts. A quick summary costs far fewer credits than a full project. See "What can I get done with 20,000 credits?" below for real examples.',
  },
  {
    q: "What can I get done with 20,000 credits?",
    a: "A lot. Roughly, 20,000 credits covers hundreds of quick tasks (channel summaries, CRM follow-ups), dozens of complex workflows (like updating your docs after a release), or a handful of full projects (like a board-ready competitive analysis). The exact amount depends on task complexity.",
    // TODO: the specific counts ("hundreds", "dozens", "a handful") must be derived from the SAME credits-per-work config as the calculator and the "What your credits get done" section, and validated against real metering before launch. Do not ship invented numbers.
  },
  {
    q: "What happens when I run out of credits?",
    a: "Misterr stops responding to new messages until your next monthly reset, and it posts a Slack message with a link to upgrade your plan. No surprise bills, no automatic top-ups: you only pay for the plan you're on. Free tier resets to 50,000 credits at the start of each month.",
  },
  {
    q: "Do unused credits roll over?",
    a: "No. Credits reset at the start of each billing cycle and don't carry over to the next month.",
  },
  {
    q: "Do different tasks use different amounts of credits?",
    a: "Yes. Simple tasks like a summary use very few credits; multi-step workflows and large deliverables use more. You only pay for work that actually gets done.",
  },
  {
    q: "Can I change plans anytime?",
    a: "Yes. Upgrades take effect immediately, so you get more credits right away. Downgrades take effect at the start of your next billing cycle.",
  },
  {
    q: "How does billing work?",
    a: "Pick monthly or annual. Monthly bills you at the start of each cycle; annual is paid upfront with a 20% discount and credits reset every month inside the year. Credits don't roll over.",
  },
  {
    q: "How much does a credit cost?",
    a: "$1.00 per 1,000 credits, the same flat rate across all plans. Anual subscriptions get 20% off. (Large teams and high-volume customers get custom volume pricing. See Enterprise below.)",
  },
  {
    q: "Is there a per-seat charge?",
    a: "No. Misterr is priced purely on usage, not on how many people use it.",
  },
  {
    q: "Can I add my whole team?",
    a: "Yes. Invite your entire team at no extra cost. You only pay for the work Misterr does, never for seats.",
  },
  {
    q: "What can Misterr access?",
    a: "Only the apps you connect, and only through secure OAuth. You decide which tools Misterr can reach, and you can revoke access at any time.",
  },
  {
    q: "Can Misterr take actions without my approval?",
    a: "Misterr handles routine tasks on its own. For sensitive actions like sending external emails, publishing, or deleting, it checks with you first. You control which actions require approval.",
    // TODO: this requires a real, user-configurable approval setting in the product. Don't publish until it exists.
  },
  {
    q: "Is my data used to train AI models?",
    a: "No. We never use your data to train AI models.",
  },
  {
    q: "Which integrations are included?",
    a: "Every integration we offer is included on all plans at no extra cost. Don't see the tool you use? Request it and we'll prioritize it.",
  },
  {
    q: "Is the free tier really free?",
    a: "Yes, and it's perpetual: 50,000 credits per month, no credit card, no time limit. Use it as long as you want; upgrade only when you outgrow it.",
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes. No contracts and no lock-in, so cancel anytime and you won't be charged for the next cycle.",
  },
  {
    q: "Do you offer Enterprise pricing?",
    a: "Yes. For large teams or high volume, we offer custom plans with volume pricing and dedicated support. Talk to sales.",
  },
];

const DEFAULT_VISIBLE = 7;

export default function Faq() {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [showAll, setShowAll] = useState(false);

  const toggle = (i: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  const visible = showAll ? FAQS : FAQS.slice(0, DEFAULT_VISIBLE);

  return (
    <section className="flex w-full justify-center bg-[#faf5f1] px-[24px] py-[80px]">
      <div className="flex w-full max-w-[1120px] flex-col gap-[40px] md:flex-row md:gap-[64px]">
        {/* Heading */}
        <div className="md:w-[300px] md:shrink-0">
          <h2 className="font-[family-name:var(--font-lexend)] text-[48px] font-semibold leading-[1] tracking-[-2.4px] text-[#191919] sm:text-[64px] sm:tracking-[-3.2px] md:sticky md:top-[100px]">
            FAQ
          </h2>
        </div>

        {/* Accordion */}
        <div className="flex flex-1 flex-col">
          {visible.map((item, i) => {
            const isOpen = open.has(i);
            return (
              <div key={i} className="border-b border-[#e3ddd5]">
                <h3>
                  <button
                    type="button"
                    onClick={() => toggle(i)}
                    aria-expanded={isOpen}
                    aria-controls={`faq-panel-${i}`}
                    id={`faq-button-${i}`}
                    className="flex w-full items-center justify-between gap-[16px] py-[20px] text-left"
                  >
                    <span className="font-[family-name:var(--font-inter)] text-[17px] font-semibold tracking-[-0.3px] text-[#191919] sm:text-[18px]">
                      {item.q}
                    </span>
                    <svg
                      className={`size-[20px] shrink-0 text-[#626262] transition-transform duration-300 ${
                        isOpen ? "rotate-180" : ""
                      }`}
                      viewBox="0 0 20 20"
                      fill="none"
                      aria-hidden
                    >
                      <path
                        d="M5 7.5 10 12.5 15 7.5"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                </h3>
                <div
                  id={`faq-panel-${i}`}
                  role="region"
                  aria-labelledby={`faq-button-${i}`}
                  className={`grid transition-all duration-300 ease-out ${
                    isOpen
                      ? "grid-rows-[1fr] opacity-100"
                      : "grid-rows-[0fr] opacity-0"
                  }`}
                >
                  <div className="overflow-hidden">
                    <p className="pb-[20px] pr-[28px] font-[family-name:var(--font-inter)] text-[15px] font-medium leading-[1.6] tracking-[-0.2px] text-[#626262] sm:text-[16px]">
                      {item.a}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}

          {FAQS.length > DEFAULT_VISIBLE && (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="mt-[24px] self-start font-[family-name:var(--font-inter)] text-[16px] font-semibold text-[#ff5200] underline-offset-4 hover:underline"
            >
              {showAll ? "Show fewer questions" : "Show more questions"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
