import Link from "next/link";

import { APP_SIGN_UP_URL } from "@/lib/app-url";
import Header from "./Header";
import Testimonials from "./Testimonials";
import WhatMisterrDoes from "./WhatMisterrDoes";
import UseCases from "./UseCases";

// Landing page imported from Figma (node 3:22).
// Assets live in /public/landing. Fonts (Lexend / Inter / Geist) are loaded in
// layout.tsx and exposed as CSS variables; Graphik (not on Google Fonts) falls
// back to Inter. Layout reproduces the 1280px Figma frame, centered.

// Integration logos for the infinite marquee (SVGs in /public/landing/logos).
const INTEGRATIONS = [
  { name: "HubSpot", file: "hubspot" },
  { name: "Salesforce", file: "salesforce" },
  { name: "Gmail", file: "gmail" },
  { name: "GitHub", file: "github" },
  { name: "Google Calendar", file: "google-calendar" },
  { name: "Notion", file: "notion" },
  { name: "Datadog", file: "datadog" },
  { name: "Metabase", file: "metabase" },
  { name: "Meta Ads", file: "meta-ads" },
  { name: "Google Ads", file: "google-ads" },
  { name: "TikTok", file: "tiktok" },
  { name: "Shopify", file: "shopify" },
  { name: "Stripe", file: "stripe" },
];

const SlackIcon = () => (
  <svg
    className="size-[20px] shrink-0"
    viewBox="0 0 122.8 122.8"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden
  >
    <path
      d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9zM32.3 77.6c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z"
      fill="#E01E5A"
    />
    <path
      d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2zM45.2 32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z"
      fill="#36C5F0"
    />
    <path
      d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2zM90.5 45.2c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z"
      fill="#2EB67D"
    />
    <path
      d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9zM77.6 90.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z"
      fill="#ECB22E"
    />
  </svg>
);

const GetStartedFree = () => (
  <Link href={APP_SIGN_UP_URL} className="group flex items-center shrink-0">
    <div className="flex h-[48px] flex-col items-start justify-center rounded-[12px] bg-[#626262] pb-[4px] shrink-0 transition-transform active:translate-y-[2px] group-hover:bg-[#cfcfcf]">
      <div className="flex flex-[1_0_0] min-h-px min-w-[119px] items-center justify-center gap-[10px] rounded-[12px] bg-[#191919] px-[17.58px] transition-colors group-hover:bg-white">
        <SlackIcon />
        <p className="font-[family-name:var(--font-inter)] text-[24px] font-medium leading-[19.2px] tracking-[-0.24px] text-white whitespace-nowrap transition-colors group-hover:text-[#191919]">
          Get started for Free
        </p>
      </div>
    </div>
  </Link>
);

const Logo = () => (
  <img
    className="block h-[28px] w-[131px] max-w-none"
    src="/landing/misterr-logo.svg"
    alt="Misterr"
  />
);

export default function HomePage() {
  return (
    <main className="flex w-full flex-col items-center overflow-x-clip bg-white">
      <Header />
      {/* ===== HERO ===== */}
      <section
        className="relative w-full overflow-clip"
        style={{
          backgroundImage:
            "linear-gradient(159.5deg, rgb(221, 242, 255) 67.234%, rgb(250, 245, 241) 89.65%), linear-gradient(90deg, rgb(221, 242, 255) 0%, rgb(221, 242, 255) 100%)",
        }}
      >
        {/* hero video: full height, always anchored to the right edge; dimmed on
            mobile so the headline stays readable over it */}
        <video
          className="pointer-events-none absolute bottom-0 right-0 top-0 z-0 h-full w-auto max-w-none object-cover object-right opacity-30 sm:opacity-100"
          autoPlay
          loop
          muted
          playsInline
          src="/landing/hero.mp4"
        />
       <div className="relative z-10 mx-auto flex min-h-[520px] w-full max-w-[1280px] flex-col items-start justify-center gap-[10px] px-[24px] pb-[56px] pt-[96px] sm:min-h-[700px] sm:pb-[80px] sm:pl-[64px] sm:pr-0 sm:pt-[112px]">
          <div className="flex w-[587px] max-w-full flex-col items-start">
            <div className="flex w-full flex-col items-start gap-[20px] sm:gap-[24px]">
              <p className="w-[462px] max-w-full font-[family-name:var(--font-lexend)] text-[56px] font-semibold leading-[52px] tracking-[-2.5px] text-[#191919] [word-break:break-word] sm:text-[98px] sm:leading-[80px] sm:tracking-[-4.9px]">
                Meet Misterr
              </p>
              <p className="w-[462px] max-w-full whitespace-pre-wrap font-[family-name:var(--font-inter)] text-[20px] font-medium leading-[28px] tracking-[-0.8px] text-[#191919] sm:text-[28px] sm:leading-[36px] sm:tracking-[-1.4px]">
                {`The AI Coworker that lives in Slack `}
                <br aria-hidden />
                and actually does the work.
              </p>
              <GetStartedFree />
            </div>
          </div>
       </div>
      </section>

      {/* fade strip */}
      <div className="relative -mt-[99px] h-[99px] w-full bg-gradient-to-b from-[rgba(250,245,241,0)] to-white" />

      {/* ===== INTEGRATIONS MARQUEE ===== */}
      <section className="flex w-full flex-col items-center gap-[40px] border-y border-[#ececec] bg-white px-[24px] py-[56px] sm:py-[64px]">
        <div className="flex flex-col items-center gap-[10px] text-center">
          <h2 className="font-[family-name:var(--font-lexend)] text-[22px] font-semibold tracking-[-0.8px] text-[#191919] sm:text-[28px] md:text-[32px]">
            Connect your entire stack
          </h2>
          <p className="font-[family-name:var(--font-inter)] text-[16px] tracking-[-0.3px] text-[#626262] md:text-[18px]">
            <span className="font-semibold text-[#ff5200]">500+</span>{" "}
            integrations and counting
          </p>
        </div>
        <div className="logo-marquee">
          <LogoGroup />
          <LogoGroup ariaHidden />
        </div>
      </section>

      {/* ===== WHAT MISTERR DOES ===== */}
      <WhatMisterrDoes />

      {/* ===== USE CASES BY TEAM ===== */}
      <section className="flex w-full flex-col items-center justify-center overflow-clip rounded-t-[8px] bg-[#faf5f1] px-[24px] py-[64px] sm:px-[40px] sm:py-[80px]">
        <UseCases />
      </section>

      {/* ===== TESTIMONIALS ===== */}
      <section className="relative flex w-full flex-col items-center justify-center gap-[40px] overflow-clip bg-gradient-to-b from-[#faf5f1] to-[#ddf2ff] px-[24px] pb-[64px] pt-[20px] sm:px-[40px] sm:pb-[80px]">
        <div className="flex min-h-[140px] w-full max-w-[1164px] flex-col items-start justify-center shrink-0 sm:h-[257px]">
          <p className="w-full font-[family-name:var(--font-lexend)] text-[36px] font-semibold tracking-[-1.8px] text-left text-[#191919] [word-break:break-word] sm:text-[56px] sm:tracking-[-2.8px] md:text-[72px] md:tracking-[-3.6px]">
            What our customers say.
          </p>
        </div>
        <Testimonials />
        <div className="absolute right-0 top-0 hidden h-[256px] w-[512px] md:block">
          <img
            className="pointer-events-none absolute inset-0 size-full max-w-none object-cover object-right"
            src="/landing/branch-sloth.png"
            alt=""
          />
        </div>
      </section>

      {/* ===== FOOTER ===== */}
      <footer className="relative isolate flex w-full flex-col items-center gap-[48px] overflow-clip bg-[#def2ff] px-[24px] pt-[64px] md:gap-[80px] md:px-[80px] md:pt-[112px]">
        <div className="z-[3] grid h-auto w-full max-w-[1120px] grid-cols-2 gap-x-[24px] gap-y-[40px] md:h-[472px] md:grid-cols-[repeat(12,minmax(0,1fr))] md:grid-rows-[repeat(2,minmax(0,1fr))]">
          {/* col 1 - brand */}
          <div className="col-span-2 flex flex-col items-start justify-between justify-self-stretch self-start md:col-[1/span_4] md:row-[1/span_2]">
            <div className="flex w-full flex-col items-start gap-[64px]">
              <Link href="/" aria-label="Misterr home">
                <Logo />
              </Link>
              <div className="flex w-full flex-wrap content-center items-center gap-x-[16px] gap-y-0">
                <a href="https://www.linkedin.com/company/misterr" aria-label="LinkedIn" className="transition-opacity hover:opacity-60">
                  <img className="size-[20px]" src="/landing/social-linkedin.svg" alt="LinkedIn" />
                </a>
                <a href="https://x.com/misterr" aria-label="X" className="transition-opacity hover:opacity-60">
                  <img className="size-[20px]" src="/landing/social-x.svg" alt="X" />
                </a>
                <a href="https://youtube.com/@misterr" aria-label="YouTube" className="transition-opacity hover:opacity-60">
                  <img className="size-[20px]" src="/landing/social-youtube.svg" alt="YouTube" />
                </a>
              </div>
            </div>
            <div className="flex min-h-[36px] w-full flex-col items-start justify-end pt-[24px] md:h-[356px] md:pt-[320px]">
              <div className="flex w-full flex-col items-start gap-[4px]">
                <p className="w-full font-[family-name:var(--font-inter)] text-[11.1px] font-normal leading-[16px] text-[#9693a3]">
                  © 2026 Misterr. All rights reserved.
                </p>
                <p className="font-[family-name:var(--font-inter)] text-[11.6px] font-normal leading-[16px] text-[#9693a3] whitespace-nowrap">
                  Your AI coworker, right inside Slack.
                </p>
              </div>
            </div>
          </div>

          {/* link columns */}
          <FooterCol
            col="md:col-[5/span_2] md:row-1"
            title="Product"
            items={[
              { label: "Overview", href: "#features" },
              { label: "Pricing", href: "/pricing" },
              { label: "FAQ", href: "#faq" },
            ]}
          />
          <FooterCol
            col="md:col-[8/span_2] md:row-1"
            title="Why Misterr"
            items={[
              { label: "vs ChatGPT", href: "#" },
              { label: "vs Copilot", href: "#" },
              { label: "vs Slack AI", href: "#" },
              { label: "vs Zapier Agents", href: "#" },
            ]}
          />
          <FooterCol
            col="md:col-[11/span_2] md:row-1"
            title="Solutions"
            items={[
              { label: "Integrations", href: "#" },
              { label: "Use cases", href: "#use-cases" },
            ]}
          />
          <FooterCol
            col="md:col-[5/span_2] md:row-2"
            title="Resources"
            items={[
              { label: "Blog", href: "#" },
              { label: "Case studies", href: "#" },
              { label: "Changelog", href: "#" },
            ]}
          />
          <FooterCol
            col="md:col-[8/span_2] md:row-2"
            title="Legal & Docs"
            items={[
              { label: "Terms of service", href: "/terms" },
              { label: "Privacy policy", href: "/privacy" },
              { label: "Docs", href: "#" },
              { label: "Imprint", href: "#" },
            ]}
          />
        </div>

        {/* decorative objects (desktop only) */}
        <div className="absolute left-1/2 top-[584px] z-[2] hidden h-[438px] w-[576.181px] -translate-x-1/2 items-center justify-center md:flex">
          <div className="rotate-180">
            <img className="pointer-events-none h-[438px] w-[576.181px] max-w-none" src="/landing/footer-object.png" alt="" />
          </div>
        </div>
        <div className="z-[1] hidden h-[331.625px] w-full flex-col items-start md:flex">
          <div className="absolute left-[-20%] right-[-20%] top-[calc(50%+111.98px)] flex aspect-[1568/980] -translate-y-1/2 flex-col items-center justify-center overflow-clip">
            <img className="h-[980px] w-[1568px] max-w-none" src="/landing/footer-blob.svg" alt="" />
          </div>
        </div>
      </footer>
    </main>
  );
}

function LogoGroup({ ariaHidden = false }: { ariaHidden?: boolean }) {
  return (
    <div className="logo-marquee__group" aria-hidden={ariaHidden}>
      {INTEGRATIONS.map((logo) => (
        <div
          key={logo.file}
          className="flex h-[36px] w-[128px] shrink-0 items-center justify-center"
        >
          <img
            src={`/landing/logos/${logo.file}.png`}
            alt={ariaHidden ? "" : logo.name}
            title={logo.name}
            className="max-h-full max-w-full object-contain opacity-65 grayscale transition duration-300 hover:opacity-100 hover:grayscale-0"
          />
        </div>
      ))}
    </div>
  );
}

function FooterCol({
  col,
  title,
  items,
}: {
  col: string;
  title: string;
  items: { label: string; href: string }[];
}) {
  return (
    <div className={`${col} flex flex-col items-start gap-[12px] justify-self-stretch self-start`}>
      <p className="w-full font-[family-name:var(--font-inter)] text-[13.5px] font-medium leading-[20px] text-[#1a182b]">
        {title}
      </p>
      <div className="flex w-full flex-col items-start gap-[10px]">
        {items.map((it) => (
          <Link
            key={it.label}
            href={it.href}
            className="flex w-full flex-col items-start py-[2px] font-[family-name:var(--font-inter)] text-[13.5px] font-normal leading-[20px] text-[#9693a3] transition-colors hover:text-[#1a182b]"
          >
            {it.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
