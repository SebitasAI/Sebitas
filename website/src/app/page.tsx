import Link from "next/link";
import Header from "./Header";
import TeamTabs from "./TeamTabs";
import Testimonials from "./Testimonials";

// Landing page imported from Figma (node 3:22).
// Assets live in /public/landing. Fonts (Lexend / Inter / Geist) are loaded in
// layout.tsx and exposed as CSS variables; Graphik (not on Google Fonts) falls
// back to Inter. Layout reproduces the 1280px Figma frame, centered.

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
  <Link href="/signup" className="flex items-center shrink-0">
    <div className="flex h-[48px] flex-col items-start justify-center rounded-[12px] bg-[#626262] pb-[4px] shrink-0 transition-transform active:translate-y-[2px]">
      <div className="flex flex-[1_0_0] min-h-px min-w-[119px] items-center justify-center gap-[10px] rounded-[12px] bg-[#191919] px-[17.58px]">
        <SlackIcon />
        <p className="font-[family-name:var(--font-inter)] text-[24px] font-medium leading-[19.2px] tracking-[-0.24px] text-white whitespace-nowrap">
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
        {/* hero video: full height, always anchored to the right edge */}
        <video
          className="pointer-events-none absolute bottom-0 right-0 top-0 z-0 h-full w-auto max-w-none object-cover object-right"
          autoPlay
          loop
          muted
          playsInline
          src="/landing/hero.mp4"
        />
       <div className="relative z-10 mx-auto flex min-h-[700px] w-full max-w-[1280px] flex-col items-start justify-center gap-[10px] pb-[80px] pl-[64px] pt-[112px]">
          <div className="flex w-[587px] max-w-full flex-col items-start">
            <div className="flex w-full flex-col items-start gap-[24px]">
              <p className="w-[462px] max-w-full font-[family-name:var(--font-lexend)] text-[98px] font-semibold leading-[80px] tracking-[-4.9px] text-[#191919] [word-break:break-word]">
                Meet Misterr
              </p>
              <p className="w-[462px] max-w-full whitespace-pre-wrap font-[family-name:var(--font-inter)] text-[28px] font-medium leading-[36px] tracking-[-1.4px] text-[#191919]">
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
      <div className="relative -mt-[99px] h-[99px] w-full bg-gradient-to-b from-[rgba(250,245,241,0)] to-[#faf5f1]" />

      {/* ===== FEATURE ROW ===== */}
      <section className="flex w-full flex-col items-center gap-[40px] overflow-clip bg-[#faf5f1] px-[60px] pb-[80px] pt-[40px]">
        <p className="min-w-full font-[family-name:var(--font-lexend)] text-[48px] font-semibold tracking-[-2.4px] text-center text-[#191919] [word-break:break-word]">
          Misterr ships real work without leaving Slack
        </p>
        <div className="flex items-center gap-[24px] shrink-0">
          {[
            {
              title: "Defense in seconds, not weeks",
              body: (
                <>
                  {`Defense is built and submitted at the moment `}
                  <br aria-hidden />a dispute comes in. No human action needed.
                </>
              ),
            },
            {
              title: "Real output, not just text.",
              body: "Most merchants recover less than 9%. Our AI wins back the revenue others leave on the table.",
            },
            {
              title: "Recover up to 70% of disputed revenue",
              body: "Most merchants recover less than 9%. Our AI wins back the revenue others leave on the table.",
            },
          ].map((c) => (
            <div key={c.title} className="flex w-[372px] flex-col items-start gap-[24px] shrink-0">
              <div className="h-[351px] w-full rounded-[12px] border border-[#191919] bg-[#ddf2ff] shadow-[0px_4px_0px_0px_#626262]" />
              <div className="flex w-full flex-col items-start gap-[12px] text-center text-[#191919]">
                <p className="w-full font-[family-name:var(--font-inter)] text-[20px] font-semibold tracking-[-1px]">
                  {c.title}
                </p>
                <p className="w-full whitespace-pre-wrap font-[family-name:var(--font-inter)] text-[16px] font-medium tracking-[-0.8px]">
                  {c.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ===== OWN RESULTS ===== */}
      <section className="flex w-full flex-col items-center justify-center gap-[40px] overflow-clip rounded-t-[8px] bg-[#faf5f1] px-[40px] py-[80px]">
        <p className="w-[660px] font-[family-name:var(--font-lexend)] text-[94px] font-semibold tracking-[-4.7px] text-center text-[#191919] [word-break:break-word]">
          Own results for every team
        </p>

        <TeamTabs />

        <div className="flex w-[1164px] flex-wrap content-center items-center gap-[24px] shrink-0">
          {[
            "Intelligent bug triage",
            "Code contributions",
            "Code contributions",
            "Code contributions",
            "Full-stack internal tools",
            "Incident + error response",
          ].map((t, i) => (
            <div key={i} className="flex w-[372px] flex-col items-start gap-[24px] shrink-0">
              <div className="h-[351px] w-full rounded-[16px] border border-black bg-white shadow-[0px_4px_0px_0px_#626262]" />
              <div className="flex w-full flex-col items-start">
                <p className="w-full font-[family-name:var(--font-inter)] text-[20px] font-semibold tracking-[-1px] text-center text-[#191919]">
                  {t}
                </p>
              </div>
            </div>
          ))}
        </div>

        <div className="flex h-[40px] items-center justify-center gap-[8px] overflow-clip rounded-[200px] px-[16px] py-[8px] shrink-0">
          <p className="font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[16px] text-[#222] whitespace-nowrap">
            And much more!
          </p>
        </div>

        <div className="flex items-center gap-[20px] py-[20px] shrink-0">
          <GetStartedFree />
          <Link
            href="#use-cases"
            className="font-[family-name:var(--font-inter)] text-[24px] font-medium leading-[19.2px] tracking-[-0.24px] text-center text-[#191919] whitespace-nowrap hover:underline"
          >
            See all use cases
          </Link>
        </div>
      </section>

      {/* ===== TESTIMONIALS ===== */}
      <section className="relative flex w-full flex-col items-center justify-center gap-[40px] overflow-clip bg-gradient-to-b from-[#faf5f1] to-[#ddf2ff] px-[40px] pb-[80px] pt-[20px]">
        <div className="flex h-[257px] flex-col items-center justify-center shrink-0">
          <p className="w-[1164px] font-[family-name:var(--font-lexend)] text-[72px] font-semibold tracking-[-3.6px] text-[#191919] [word-break:break-word]">
            What our clients
            <br aria-hidden />
            {`say about Misterr. `}
          </p>
        </div>
        <Testimonials />
        <div className="absolute right-0 top-0 h-[256px] w-[512px]">
          <img
            className="pointer-events-none absolute inset-0 size-full max-w-none object-cover object-right"
            src="/landing/branch-sloth.png"
            alt=""
          />
        </div>
      </section>

      {/* ===== SECURITY ===== */}
      <section className="flex w-full flex-col items-center gap-[20px] bg-[#ddf2ff] px-[58px] py-[60px]">
        <div className="flex w-full items-center justify-between overflow-clip rounded-[12px] border border-[#191919] bg-white pb-[30px] pl-[40px] pr-[20px] pt-[40px] shadow-[0px_4px_0px_0px_#626262]">
          <div className="flex w-[539px] flex-col items-start gap-[20px] font-medium text-[#191919] shrink-0">
            <p className="font-[family-name:var(--font-lexend)] text-[40px] leading-[1.2] tracking-[-1.2px] whitespace-nowrap">
              Enterprise grade
              <br aria-hidden />
              {`security & privacity`}
            </p>
            <p className="min-w-full font-[family-name:var(--font-inter)] text-[18px] leading-[1.4] tracking-[-0.36px]">
              We take security and compliance seriously. Supersonik is SOC 2 Type
              II and GDPR compliant, trusted by thousands of businesses to build
              secure and compliant AI Agents.
            </p>
          </div>
          <div className="inline-grid grid-cols-[max-content] grid-rows-[max-content] place-items-start leading-[0] shrink-0">
            <div className="col-1 row-1 ml-0 mt-0 h-[201px] w-[200px]">
              <img
                className="pointer-events-none absolute inset-0 size-full max-w-none object-cover"
                src="/landing/badge-soc2.png"
                alt="SOC 2"
              />
            </div>
            <div className="col-1 row-1 ml-[210px] mt-0 h-[201px] w-[200px]">
              <img
                className="pointer-events-none absolute inset-0 size-full max-w-none object-cover"
                src="/landing/badge-gdpr.png"
                alt="GDPR"
              />
            </div>
          </div>
        </div>
        <div className="flex w-full flex-col items-center">
          <div className="flex w-full items-end justify-between font-[family-name:var(--font-inter)] text-[18px] font-normal leading-[1.4] tracking-[-0.36px] text-[#191919] [word-break:break-word]">
            <p className="w-[454px]">Misterr is commited to safeguarding your data.</p>
            <p className="whitespace-nowrap">Learn more</p>
          </div>
        </div>
      </section>

      {/* ===== FOOTER ===== */}
      <footer className="relative isolate flex w-full flex-col items-center gap-[80px] overflow-clip bg-[#def2ff] px-[80px] pt-[112px]">
        <div className="z-[3] grid h-[472px] w-full max-w-[1120px] grid-cols-[repeat(12,minmax(0,1fr))] grid-rows-[repeat(2,minmax(0,1fr))] gap-x-[24px] gap-y-[40px]">
          {/* col 1 - brand */}
          <div className="col-[1/span_4] row-[1/span_2] flex flex-col items-start justify-between justify-self-stretch self-start">
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
            <div className="flex h-[356px] min-h-[36px] w-full flex-col items-start justify-end pt-[320px]">
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
            col="col-[5/span_2] row-1"
            title="Product"
            items={[
              { label: "Overview", href: "#features" },
              { label: "Pricing", href: "#pricing" },
              { label: "FAQ", href: "#faq" },
            ]}
          />
          <FooterCol
            col="col-[8/span_2] row-1"
            title="Why Misterr"
            items={[
              { label: "vs ChatGPT", href: "#" },
              { label: "vs Copilot", href: "#" },
              { label: "vs Slack AI", href: "#" },
              { label: "vs Zapier Agents", href: "#" },
            ]}
          />
          <FooterCol
            col="col-[11/span_2] row-1"
            title="Solutions"
            items={[
              { label: "Integrations", href: "#" },
              { label: "Use cases", href: "#use-cases" },
            ]}
          />
          <FooterCol
            col="col-[5/span_2] row-2"
            title="Company"
            items={[
              { label: "Partner program", href: "#" },
              { label: "Affiliate program", href: "#" },
              { label: "About us", href: "#" },
              { label: "Brand kit", href: "#" },
              { label: "Careers", href: "#" },
            ]}
          />
          <FooterCol
            col="col-[8/span_2] row-2"
            title="Resources"
            items={[
              { label: "Blog", href: "#" },
              { label: "Case studies", href: "#" },
              { label: "Changelog", href: "#" },
            ]}
          />
          <FooterCol
            col="col-[11/span_2] row-2"
            title="Legal & Docs"
            items={[
              { label: "Terms of service", href: "/terms" },
              { label: "Privacy policy", href: "/privacy" },
              { label: "Docs", href: "#" },
              { label: "Imprint", href: "#" },
            ]}
          />
        </div>

        {/* decorative objects */}
        <div className="absolute left-1/2 top-[584px] z-[2] flex h-[438px] w-[576.181px] -translate-x-1/2 items-center justify-center">
          <div className="rotate-180">
            <img className="pointer-events-none h-[438px] w-[576.181px] max-w-none" src="/landing/footer-object.png" alt="" />
          </div>
        </div>
        <div className="z-[1] flex h-[331.625px] w-full flex-col items-start">
          <div className="absolute left-[-20%] right-[-20%] top-[calc(50%+111.98px)] flex aspect-[1568/980] -translate-y-1/2 flex-col items-center justify-center overflow-clip">
            <img className="h-[980px] w-[1568px] max-w-none" src="/landing/footer-blob.svg" alt="" />
          </div>
        </div>
      </footer>
    </main>
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
