// The 5-tier (+ Enterprise) plan ladder rendered as cards under the
// calculator. Numbers match `app/billing/plans.py`: 1 credit = $0.001 USD,
// 20% annual discount, sliders within tier scale credits and price linearly.
//
// CTAs: Free routes to the Slack install (NEXT_PUBLIC_GET_STARTED_URL or
// hardcoded fallback). Paid tiers also start with a sign-up that lands the
// user in `/settings/billing` where the real Stripe Checkout lives.
// Enterprise is contact-only.

import Link from "next/link";

const GET_STARTED_HREF = "/signup";
const SALES_HREF = "mailto:sales@misterr.ai?subject=Misterr%20Enterprise";

const nf = new Intl.NumberFormat("en-US");
const fmt = (n: number) => nf.format(Math.round(n));

type Tier = {
  name: string;
  priceLabel: string;
  priceSub?: string;
  creditsLabel: string;
  blurb: string;
  features: string[];
  cta: { label: string; href: string };
  highlight?: boolean; // visual emphasis (the recommended tier)
};

const TIERS: Tier[] = [
  {
    name: "Free",
    priceLabel: "$0",
    priceSub: "/mo, perpetuo",
    creditsLabel: "50,000 credits / mo",
    blurb: "Para probar Misterr sin compromiso.",
    features: [
      "3 integraciones activas",
      "5 automations",
      "10 scheduled tasks",
      "Soporte community",
    ],
    cta: { label: "Empezar gratis", href: GET_STARTED_HREF },
  },
  {
    name: "Starter",
    priceLabel: "$100",
    priceSub: "/mo desde",
    creditsLabel: "100k – 300k credits / mo",
    blurb: "Para equipos chicos automatizando day-to-day.",
    features: [
      "Integraciones ilimitadas",
      "Automations ilimitadas",
      "Scheduled tasks ilimitados",
      "Soporte email",
    ],
    cta: { label: "Empezar Starter", href: GET_STARTED_HREF },
  },
  {
    name: "Pro",
    priceLabel: "$400",
    priceSub: "/mo desde",
    creditsLabel: "400k – 1M credits / mo",
    blurb: "Para equipos que quieren custom skills + API.",
    features: [
      "Todo lo de Starter",
      "Custom skills builder",
      "Workspace analytics",
      "API access",
      "Soporte prioritario",
    ],
    cta: { label: "Empezar Pro", href: GET_STARTED_HREF },
    highlight: true,
  },
  {
    name: "Scale",
    priceLabel: "$1,500",
    priceSub: "/mo desde",
    creditsLabel: "1.5M – 3M credits / mo",
    blurb: "Para empresas mid-market con varios workspaces.",
    features: [
      "Todo lo de Pro",
      "Multi-workspace",
      "Soporte por Slack",
      "Onboarding dedicado",
    ],
    cta: { label: "Empezar Scale", href: GET_STARTED_HREF },
  },
  {
    name: "Business",
    priceLabel: "$5,000",
    priceSub: "/mo desde",
    creditsLabel: "5M – 10M credits / mo",
    blurb: "Para empresas que necesitan SSO, audit y SLA.",
    features: [
      "Todo lo de Scale",
      "SSO / SAML",
      "Audit log + RBAC",
      "SLA 99.9%",
      "Customer Success Manager",
    ],
    cta: { label: "Empezar Business", href: GET_STARTED_HREF },
  },
  {
    name: "Enterprise",
    priceLabel: "Custom",
    creditsLabel: "Volumen + descuento",
    blurb: "Para implementaciones grandes con requisitos específicos.",
    features: [
      "Todo lo de Business",
      "Integraciones custom",
      "SLA 99.99%",
      "Retención y compliance a medida",
      "Descuento por volumen",
    ],
    cta: { label: "Hablar con Sales", href: SALES_HREF },
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
          Empezá gratis. Subí cuando el equipo lo necesite. Anual: 20% off.
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
