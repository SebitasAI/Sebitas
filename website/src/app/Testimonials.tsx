import type { ReactNode } from "react";

// TODO testimonials: every entry must be a REAL, permissioned customer quote about Misterr,
// with the customer's real name, role, company, and (if shown) a real metric and LinkedIn profile.
// Fabricated testimonials are illegal under the FTC's 2024 rule on fake endorsements and are a
// serious risk during fundraising diligence. If there are no real quotes yet, hide this section.

type Testimonial = {
  savedMetric: string;
  quote: string;
  name: string;
  role: string;
  company: string;
  photoUrl: string;
  linkedinUrl: string;
};

// Obvious placeholders so nothing ships by accident. Replace with real,
// permissioned quotes (or hide the section) before launch.
const TESTIMONIALS: Testimonial[] = [
  {
    savedMetric: "[verified metric]",
    quote: "[Real customer quote about Misterr, in their words]",
    name: "[Full name]",
    role: "[Role]",
    company: "[Company]",
    photoUrl: "",
    linkedinUrl: "",
  },
  {
    savedMetric: "[verified metric]",
    quote: "[A second real, on-product quote from a Misterr customer]",
    name: "[Full name]",
    role: "[Role]",
    company: "[Company]",
    photoUrl: "",
    linkedinUrl: "",
  },
  {
    savedMetric: "[verified metric]",
    quote: "[A third permissioned quote about what Misterr shipped]",
    name: "[Full name]",
    role: "[Role]",
    company: "[Company]",
    photoUrl: "",
    linkedinUrl: "",
  },
];

export default function Testimonials() {
  return (
    <div className="flex w-full max-w-[1164px] flex-col items-stretch gap-[24px] md:flex-row md:items-center">
      {TESTIMONIALS.map((t, i) => (
        <Card key={i} t={t} featured={i === 1} />
      ))}
    </div>
  );
}

function Card({ t, featured }: { t: Testimonial; featured: boolean }) {
  return (
    <article
      className={`flex flex-1 flex-col gap-[20px] rounded-[20px] border p-[24px] backdrop-blur-[14px] sm:p-[28px] ${
        featured
          ? "border-white/80 bg-white/70 shadow-[0px_16px_40px_rgba(70,80,150,0.18)] md:-translate-y-[14px] md:scale-[1.04]"
          : "border-white/50 bg-white/35 shadow-[0px_10px_30px_rgba(70,80,150,0.12)]"
      }`}
    >
      {/* metric pill row */}
      <div className="flex items-center gap-[8px]">
        <Clock className="size-[16px] text-[#5b6080]" />
        <span className="font-[family-name:var(--font-inter)] text-[13px] font-medium text-[#5b6080]">
          Saved:
        </span>
        <span className="rounded-full bg-[#191919] px-[10px] py-[3px] font-[family-name:var(--font-inter)] text-[12px] font-semibold text-white">
          {t.savedMetric}
        </span>
      </div>

      {/* quote */}
      <p className="flex-1 font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[1.5] tracking-[-0.3px] text-[#191919]">
        &ldquo;{t.quote}&rdquo;
      </p>

      {/* attribution */}
      <div className="flex items-center gap-[12px]">
        {t.photoUrl && (
          <img
            src={t.photoUrl}
            alt={t.name}
            className="size-[44px] shrink-0 rounded-full object-cover"
          />
        )}
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-[family-name:var(--font-inter)] text-[15px] font-semibold tracking-[-0.2px] text-[#191919]">
            {t.name}
          </span>
          <span className="truncate font-[family-name:var(--font-inter)] text-[13px] font-medium text-[#626262]">
            {t.role}, {t.company}
          </span>
        </div>
        {t.linkedinUrl && (
          <a
            href={t.linkedinUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`${t.name} on LinkedIn`}
            className="ml-auto flex size-[28px] shrink-0 items-center justify-center rounded-[6px] text-[#0a66c2] transition-colors hover:bg-[#0a66c2]/10"
          >
            <LinkedIn className="size-[18px]" />
          </a>
        )}
      </div>
    </article>
  );
}

function Clock({ className }: { className?: string }): ReactNode {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function LinkedIn({ className }: { className?: string }): ReactNode {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden>
      <path d="M4.98 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM3 9h4v12H3zM9 9h3.8v1.7h.05c.53-1 1.83-2.05 3.77-2.05 4.03 0 4.78 2.65 4.78 6.1V21h-4v-5.4c0-1.3 0-2.95-1.8-2.95s-2.08 1.4-2.08 2.86V21H9z" />
    </svg>
  );
}
