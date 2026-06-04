// Shared layout for every /solutions/<slug> page. Each solution feeds
// the same shape into this component:
//
//   { eyebrow, title, lede, features[4], outcomes[3] }
//
// All five pages render the same structural sections (Hero, four
// features, three outcomes, closing CTA) so the marketing story stays
// consistent and the per-page file stays a config object plus this
// component call.
//
// CTAs route to APP_SIGN_UP_URL (workspace install starts there) and
// BOOK_SALES_URL (Google Calendar appointment scheduling). Same
// constants the Header + Pricing CTAs already use.

import Link from "next/link";
import type { ReactNode } from "react";

import Footer from "../../Footer";
import Header from "../../Header";
import { APP_SIGN_UP_URL, BOOK_SALES_URL } from "@/lib/app-url";


export type SolutionFeature = {
  title: string;
  description: string;
};

export type SolutionOutcome = {
  metric: string; // headline number, eg. "12h"
  label: string;  // unit / context, eg. "saved per analyst per week"
};

export type SolutionPageProps = {
  eyebrow: string;     // sits above the H1, eg. "FOR OPERATIONS TEAMS"
  title: string;       // H1
  lede: string;        // paragraph under the title
  intro?: ReactNode;   // optional richer intro block
  features: SolutionFeature[]; // 4 cards
  outcomes?: SolutionOutcome[]; // 3 metric callouts (optional)
};


export default function SolutionPage({
  eyebrow,
  title,
  lede,
  intro,
  features,
  outcomes,
}: SolutionPageProps) {
  return (
    <main className="flex min-h-screen w-full flex-col items-center overflow-x-clip bg-white">
      <Header />

      {/* Hero */}
      <section className="flex w-full flex-col items-center gap-[18px] bg-gradient-to-b from-[#ddf2ff] to-white px-[24px] pb-[40px] pt-[160px] text-center">
        <span className="rounded-full bg-white/70 px-[14px] py-[6px] font-[family-name:var(--font-inter)] text-[12px] font-semibold uppercase tracking-[2px] text-[#4a4a4a] backdrop-blur">
          {eyebrow}
        </span>
        <h1 className="max-w-[820px] font-[family-name:var(--font-lexend)] text-[40px] font-semibold leading-[1.05] tracking-[-2px] text-[#191919] sm:text-[56px] sm:tracking-[-2.8px]">
          {title}
        </h1>
        <p className="max-w-[640px] font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[1.45] tracking-[-0.4px] text-[#626262]">
          {lede}
        </p>
        <div className="mt-[8px] flex flex-wrap items-center justify-center gap-[10px]">
          <Link
            href={APP_SIGN_UP_URL}
            className="rounded-[12px] bg-[#ff5200] px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-white shadow-[0px_4px_0px_0px_#cc4a00] transition active:translate-y-[2px] hover:bg-[#ff6a23]"
          >
            Empezar gratis
          </Link>
          <a
            href={BOOK_SALES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-[12px] border border-[#191919] bg-white px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-[#191919] transition hover:bg-[#191919] hover:text-white"
          >
            Talk to sales
          </a>
        </div>
        {intro && (
          <div className="mt-[12px] max-w-[640px] font-[family-name:var(--font-inter)] text-[15px] leading-[1.55] text-[#626262]">
            {intro}
          </div>
        )}
      </section>

      {/* Outcomes (metric callouts) */}
      {outcomes && outcomes.length > 0 && (
        <section className="w-full bg-white px-[24px] py-[60px]">
          <div className="mx-auto grid w-full max-w-[1080px] gap-[16px] sm:grid-cols-3">
            {outcomes.map((o) => (
              <div
                key={o.metric + o.label}
                className="flex flex-col items-start gap-[6px] rounded-[16px] border border-[#191919] bg-white p-[24px] shadow-[0px_4px_0px_0px_#626262]"
              >
                <p className="font-[family-name:var(--font-lexend)] text-[40px] font-semibold leading-[1.0] tracking-[-1.6px] text-[#ff5200]">
                  {o.metric}
                </p>
                <p className="font-[family-name:var(--font-inter)] text-[14px] leading-[1.45] text-[#191919]">
                  {o.label}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Features grid */}
      <section className="flex w-full flex-col items-center bg-[#faf5f1] px-[24px] py-[60px]">
        <div className="mb-[28px] flex flex-col items-center gap-[8px] text-center">
          <h2 className="font-[family-name:var(--font-lexend)] text-[28px] font-semibold tracking-[-1px] text-[#191919] sm:text-[36px]">
            Lo que Misterr hace
          </h2>
          <p className="max-w-[560px] font-[family-name:var(--font-inter)] text-[15px] font-medium text-[#626262]">
            Tareas que dejan de robarle horas al equipo, hechas desde Slack.
          </p>
        </div>
        <div className="grid w-full max-w-[1080px] gap-[16px] sm:grid-cols-2">
          {features.map((f) => (
            <div
              key={f.title}
              className="flex flex-col gap-[10px] rounded-[16px] border border-[#191919] bg-white p-[24px] shadow-[0px_4px_0px_0px_#626262]"
            >
              <h3 className="font-[family-name:var(--font-lexend)] text-[20px] font-semibold tracking-[-0.6px] text-[#191919]">
                {f.title}
              </h3>
              <p className="font-[family-name:var(--font-inter)] text-[15px] leading-[1.5] text-[#4a4a4a]">
                {f.description}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Closing CTA */}
      <section className="flex w-full flex-col items-center gap-[16px] bg-white px-[24px] py-[80px] text-center">
        <h2 className="max-w-[640px] font-[family-name:var(--font-lexend)] text-[28px] font-semibold tracking-[-1px] text-[#191919] sm:text-[36px]">
          ¿Listo para probarlo en tu equipo?
        </h2>
        <p className="max-w-[520px] font-[family-name:var(--font-inter)] text-[16px] text-[#626262]">
          Empezá gratis con 50,000 créditos al mes. Sin tarjeta, sin compromiso.
        </p>
        <div className="mt-[8px] flex flex-wrap items-center justify-center gap-[10px]">
          <Link
            href={APP_SIGN_UP_URL}
            className="rounded-[12px] bg-[#ff5200] px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-white shadow-[0px_4px_0px_0px_#cc4a00] transition active:translate-y-[2px] hover:bg-[#ff6a23]"
          >
            Empezar gratis
          </Link>
          <a
            href={BOOK_SALES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-[12px] border border-[#191919] bg-white px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-[#191919] transition hover:bg-[#191919] hover:text-white"
          >
            Talk to sales
          </a>
        </div>
      </section>

      <Footer />
    </main>
  );
}
