import type { Metadata } from "next";
import Header from "../Header";
import PriceCalculator from "./PriceCalculator";

export const metadata: Metadata = {
  title: "Pricing — Misterr",
  description:
    "Usage-based pricing for Misterr. Estimate your monthly credits and cost with the interactive calculator.",
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
          Pay only for what Misterr actually does. $2,50 per 1.000 credits — move
          the slider to estimate your month.
        </p>
      </section>

      {/* Calculator */}
      <section className="flex w-full flex-col items-center bg-white px-[24px] pb-[100px] pt-[16px]">
        <PriceCalculator />
      </section>
    </main>
  );
}
