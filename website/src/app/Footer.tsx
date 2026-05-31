import Link from "next/link";

// Shared site footer (same one used on the landing page).
export default function Footer() {
  return (
    <footer className="relative isolate flex w-full flex-col items-center gap-[48px] overflow-clip bg-[#def2ff] px-[24px] pt-[64px] md:gap-[80px] md:px-[80px] md:pt-[112px]">
      <div className="z-[3] grid h-auto w-full max-w-[1120px] grid-cols-2 gap-x-[24px] gap-y-[40px] md:h-[472px] md:grid-cols-[repeat(12,minmax(0,1fr))] md:grid-rows-[repeat(2,minmax(0,1fr))]">
        {/* col 1 - brand */}
        <div className="col-span-2 flex flex-col items-start justify-between justify-self-stretch self-start md:col-[1/span_4] md:row-[1/span_2]">
          <div className="flex w-full flex-col items-start gap-[64px]">
            <Link href="/" aria-label="Misterr home">
              <img
                className="block h-[28px] w-[131px] max-w-none"
                src="/landing/misterr-logo.svg"
                alt="Misterr"
              />
            </Link>
            <div className="flex w-full flex-wrap content-center items-center gap-x-[16px] gap-y-0">
              <a
                href="https://www.linkedin.com/company/misterr"
                aria-label="LinkedIn"
                className="transition-opacity hover:opacity-60"
              >
                <img className="size-[20px]" src="/landing/social-linkedin.svg" alt="LinkedIn" />
              </a>
              <a
                href="https://x.com/misterr"
                aria-label="X"
                className="transition-opacity hover:opacity-60"
              >
                <img className="size-[20px]" src="/landing/social-x.svg" alt="X" />
              </a>
              <a
                href="https://youtube.com/@misterr"
                aria-label="YouTube"
                className="transition-opacity hover:opacity-60"
              >
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
            { label: "Overview", href: "/#features" },
            { label: "Pricing", href: "/pricing" },
            { label: "FAQ", href: "/pricing#faq" },
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
          col="md:col-[8/span_2] md:row-2"
          title="Resources"
          items={[
            { label: "Blog", href: "#" },
            { label: "Case studies", href: "#" },
            { label: "Changelog", href: "#" },
          ]}
        />
        <FooterCol
          col="md:col-[11/span_2] md:row-2"
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
          <img
            className="pointer-events-none h-[438px] w-[576.181px] max-w-none"
            src="/landing/footer-object.png"
            alt=""
          />
        </div>
      </div>
      <div className="z-[1] hidden h-[331.625px] w-full flex-col items-start md:flex">
        <div className="absolute left-[-20%] right-[-20%] top-[calc(50%+111.98px)] flex aspect-[1568/980] -translate-y-1/2 flex-col items-center justify-center overflow-clip">
          <img
            className="h-[980px] w-[1568px] max-w-none"
            src="/landing/footer-blob.svg"
            alt=""
          />
        </div>
      </div>
    </footer>
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
