import type { Metadata } from "next";
import Header from "../Header";
import Footer from "../Footer";
import PriceCalculator from "./PriceCalculator";
import CreditsValue from "./CreditsValue";
import TierLadder from "./TierLadder";
import Faq from "./Faq";

export const metadata: Metadata = {
  title: "Pricing | Misterr",
  description:
    "Misterr pricing: start free with 50,000 credits/mo. Pay $1 per 1,000 credits. No per-user fees, no surprises.",
};

export default function PricingPage() {
  return (
    <main className="flex min-h-screen w-full flex-col items-center overflow-x-clip bg-white">
      <Header />

      {/* Hero */}
      <section className="flex w-full flex-col items-center gap-[18px] bg-gradient-to-b from-[#ddf2ff] to-white px-[24px] pb-[40px] pt-[160px] text-center">
        <h1 className="font-[family-name:var(--font-lexend)] text-[40px] font-semibold leading-[1.05] tracking-[-2px] text-[#191919] sm:text-[56px] sm:tracking-[-2.8px]">
          Pricing that scales with you
        </h1>
        <p className="max-w-[560px] font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[1.45] tracking-[-0.4px] text-[#626262]">
          Start free with 50,000 credits a month. Pay $1 per 1,000 extra
          credits. No per-user fees, no surprises.
        </p>
      </section>

      {/* Calculator */}
      <section className="flex w-full flex-col items-center bg-white px-[24px] pb-[60px] pt-[16px]">
        <PriceCalculator />
      </section>

      {/* 5-tier ladder + Enterprise */}
      <TierLadder />

      {/* What your credits get done */}
      <CreditsValue />

      {/* FAQ */}
      <Faq />

      {/* Footer */}
      <Footer />
    </main>
  );
}
