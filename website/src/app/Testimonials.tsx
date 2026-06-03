import type { ReactNode } from "react";

// TODO testimonials: every entry must be a REAL, permissioned customer quote with the customer's
// real name, role, photo, and LinkedIn. Fabricated testimonials (invented names/faces/metrics)
// are illegal under the FTC's 2024 rule on fake endorsements and a serious fundraising-diligence
// risk. If there are no real quotes yet, hide this section rather than filling it.

type Testimonial = {
  savedMetric: string;
  quote: string;
  name: string;
  role: string;
  photoUrl: string;
  linkedinUrl: string;
};

// ILLUSTRATIVE SAMPLES: fictional names, invented quotes/metrics for design only.
// These are NOT real customers. Do NOT ship them as real endorsements (see the FTC TODO above).
// Replace with real, permissioned quotes (and real photo + LinkedIn) before launch, or hide the
// section. Photo and LinkedIn are intentionally left empty so no fake face or dead link is shown.
const TESTIMONIALS: Testimonial[] = [
  {
    savedMetric: "3 hrs/week",
    quote:
      "Misterr summarizes my whole #support channel and drafts the follow-ups. It saves me about 3 hours a week, all from Slack.",
    name: "Marco Ferreira",
    role: "Support Lead",
    photoUrl: "",
    linkedinUrl: "",
  },
  {
    savedMetric: "6 doc pages",
    quote:
      "I asked Misterr to update our docs after a release and it had a clean preview ready before my coffee. I genuinely love it.",
    name: "Lena Whitfield",
    role: "Engineering Manager",
    photoUrl: "",
    linkedinUrl: "",
  },
  {
    savedMetric: "an afternoon",
    quote:
      "Gave Misterr a one-line brief and got back a board-ready PDF, not a wall of text. It saved me a whole afternoon.",
    name: "Priya Anand",
    role: "Chief of Staff",
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
            {t.role}
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
