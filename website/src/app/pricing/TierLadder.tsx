// The 5-tier (+ Enterprise) plan ladder rendered as cards under the
// calculator. Numbers match `app/billing/plans.py`: 1 credit = $0.001 USD,
// 20% annual discount, sliders within tier scale credits and price linearly.
//
// CTAs: Free routes to the Slack install (NEXT_PUBLIC_GET_STARTED_URL or
// hardcoded fallback). Paid tiers also start with a sign-up that lands the
// user in `/settings/billing` where the real Stripe Checkout lives.
// Enterprise is contact-only.

import Link from "next/link";

import { APP_SIGN_UP_URL, BOOK_SALES_URL } from "@/lib/app-url";

const GET_STARTED_HREF = APP_SIGN_UP_URL;
const SALES_HREF = BOOK_SALES_URL;

const nf = new Intl.NumberFormat("en-US");
const fmt = (n: number) => nf.format(Math.round(n));

type Tier = {
  name: string;
  priceLabel: string;
  priceSub?: string;
  creditsLabel: string;
  blurb: string;
  features: string[];
  cta: { label: string; href: string; external?: boolean };
  highlight?: boolean; // visual emphasis (the recommended tier)
};

const TIERS: Tier[] = [
  {
    name: "Free",
    priceLabel: "$0",
    priceSub: "/mo, forever",
    creditsLabel: "50,000 credits / mo",
    blurb: "To try Misterr with no commitment.",
    features: [
      "3 active integrations",
      "5 automations",
      "10 scheduled tasks",
      "Community support",
    ],
    cta: { label: "Start for free", href: GET_STARTED_HREF },
  },
  {
    name: "Starter",
    priceLabel: "$100",
    priceSub: "/mo from",
    creditsLabel: "100k – 300k credits / mo",
    blurb: "For small teams automating the day-to-day.",
    features: [
      "Unlimited integrations",
      "Unlimited automations",
      "Unlimited scheduled tasks",
      "Email support",
    ],
    cta: { label: "Start Starter", href: GET_STARTED_HREF },
  },
  {
    name: "Pro",
    priceLabel: "$400",
    priceSub: "/mo from",
    creditsLabel: "400k – 1M credits / mo",
    blurb: "For teams that want custom skills + API.",
    features: [
      "Everything in Starter",
      "Custom skills builder",
      "Workspace analytics",
      "API access",
      "Priority support",
    ],
    cta: { label: "Start Pro", href: GET_STARTED_HREF },
    highlight: true,
  },
  {
    name: "Scale",
    priceLabel: "$1,500",
    priceSub: "/mo from",
    creditsLabel: "1.5M – 3M credits / mo",
    blurb: "For mid-market companies with multiple workspaces.",
    features: [
      "Everything in Pro",
      "Multi-workspace",
      "Slack support",
      "Dedicated onboarding",
    ],
    cta: { label: "Start Scale", href: GET_STARTED_HREF },
  },
  {
    name: "Business",
    priceLabel: "$5,000",
    priceSub: "/mo from",
    creditsLabel: "5M – 10M credits / mo",
    blurb: "For companies that need SSO, audit, and SLA.",
    features: [
      "Everything in Scale",
      "SSO / SAML",
      "Audit log + RBAC",
      "SLA 99.9%",
      "Customer Success Manager",
    ],
    cta: { label: "Start Business", href: GET_STARTED_HREF },
  },
  {
    name: "Enterprise",
    priceLabel: "Custom",
    creditsLabel: "Volume + discount",
    blurb: "For large deployments with specific requirements.",
    features: [
      "Everything in Business",
      "Custom integrations",
      "SLA 99.99%",
      "Custom retention and compliance",
      "Volume discount",
    ],
    cta: { label: "Talk to sales", href: SALES_HREF, external: true },
  },
];


export default function TierLadder() {
  return (
    <section className="flex w-full flex-col items-center gap-[24px] bg-[#faf5f1] px-[24px] py-[60px]">
      <div className="flex flex-col items-center gap-[8px] text-center">
        <h2 className="font-[family-name:var(--font-lexend)] text-[32px] font-semibold tracking-[-1.2px] text-[#191919] sm:text-[40px]">
          Plans
        </h2>
        <p className="max-w-[560px] font-[family-name:var(--font-inter)] text-[16px] font-medium text-[#626262]">
          Start free. Upgrade when your team needs to. Annual: 20% off.
        </p>
      </div>

      <div className="grid w-full max-w-[1180px] grid-cols-1 gap-[16px] sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {TIERS.map((tier) => (
          <TierCard key={tier.name} tier={tier} />
        ))}
      </div>
    </section>
  );
}


function TierCard({ tier }: { tier: Tier }) {
  const isHighlight = !!tier.highlight;
  return (
    <div
      className={`flex flex-col gap-[18px] rounded-[18px] border bg-white p-[20px] ${
        isHighlight
          ? "border-[#ff5200] shadow-[0px_4px_0px_0px_#ff5200]"
          : "border-[#191919] shadow-[0px_4px_0px_0px_#626262]"
      }`}
    >
      <div className="flex items-baseline justify-between">
        <h3 className="font-[family-name:var(--font-lexend)] text-[20px] font-semibold tracking-[-0.6px] text-[#191919]">
          {tier.name}
        </h3>
        {isHighlight && (
          <span className="rounded-full bg-[#ff5200] px-[10px] py-[2px] font-[family-name:var(--font-inter)] text-[10px] font-semibold uppercase tracking-[0.4px] text-white">
            Popular
          </span>
        )}
      </div>

      <div className="flex flex-col gap-[4px]">
        <p className="font-[family-name:var(--font-lexend)] text-[32px] font-semibold leading-[1.0] tracking-[-1.2px] text-[#191919]">
          {tier.priceLabel}
          {tier.priceSub && (
            <span className="ml-[6px] font-[family-name:var(--font-inter)] text-[14px] font-medium text-[#9a9a9a]">
              {tier.priceSub}
            </span>
          )}
        </p>
        <p className="font-[family-name:var(--font-inter)] text-[13px] font-medium text-[#4a4a4a]">
          {tier.creditsLabel}
        </p>
      </div>

      <p className="font-[family-name:var(--font-inter)] text-[13px] leading-[1.45] text-[#626262]">
        {tier.blurb}
      </p>

      <ul className="flex flex-col gap-[8px]">
        {tier.features.map((f) => (
          <li
            key={f}
            className="flex items-start gap-[8px] font-[family-name:var(--font-inter)] text-[13px] leading-[1.45] text-[#191919]"
          >
            <span aria-hidden className="mt-[6px] inline-block size-[5px] rounded-full bg-[#ff5200]" />
            <span>{f}</span>
          </li>
        ))}
      </ul>

      <Link
        href={tier.cta.href}
        target={tier.cta.external ? "_blank" : undefined}
        rel={tier.cta.external ? "noopener noreferrer" : undefined}
        className={`mt-auto inline-flex items-center justify-center rounded-full px-[16px] py-[10px] font-[family-name:var(--font-inter)] text-[14px] font-semibold tracking-[-0.2px] transition ${
          isHighlight
            ? "bg-[#ff5200] text-white hover:bg-[#e64a00]"
            : "border border-[#191919] bg-white text-[#191919] hover:bg-[#191919] hover:text-white"
        }`}
      >
        {tier.cta.label}
      </Link>
    </div>
  );
}
