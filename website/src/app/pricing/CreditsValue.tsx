// TODO pricing: the per-task-type credit ranges are UNVALIDATED placeholders.
// They are a public promise to the customer: real metering must debit what is shown here,
// or there will be billing disputes. Confirm with real data and align with the calculator
// before publishing. DO NOT ship these ranges as final.

import type { ReactNode } from "react";

// Ranges derive from the SAME unit the calculator uses ("1 task = N credits"),
// so this scale never contradicts the slider. Change the base in one place.
const CREDITS_PER_TASK = 50; // must match PriceCalculator's defaultCreditsPerTask

const nf = new Intl.NumberFormat("en-US");
const fmt = (n: number) => nf.format(Math.round(n));

const TIERS = [
  {
    key: "quick",
    label: "Quick tasks",
    min: CREDITS_PER_TASK * 0.5, // 25
    max: CREDITS_PER_TASK * 2, // 100
    desc: "Lookups, summaries and quick answers — minutes of work.",
    tint: "bg-[#ddf2ff]",
  },
  {
    key: "complex",
    label: "Complex workflows",
    min: CREDITS_PER_TASK * 5, // 250
    max: CREDITS_PER_TASK * 20, // 1,000
    desc: "Multi-step jobs across tools, done end to end.",
    tint: "bg-[#faf5f1]",
  },
  {
    key: "full",
    label: "Full projects",
    min: CREDITS_PER_TASK * 40, // 2,000
    max: CREDITS_PER_TASK * 200, // 10,000
    desc: "Large deliverables you'd hand to a teammate for a day.",
    tint: "bg-[#fff3ec]",
  },
] as const;

export default function CreditsValue() {
  return (
    <section className="flex w-full flex-col items-center gap-[40px] bg-white px-[24px] pb-[100px] pt-[40px]">
      <div className="flex flex-col items-center gap-[12px] text-center">
        <h2 className="font-[family-name:var(--font-lexend)] text-[28px] font-semibold tracking-[-1px] text-[#191919] sm:text-[36px] sm:tracking-[-1.6px]">
          What your credits get done
        </h2>
        <p className="max-w-[560px] font-[family-name:var(--font-inter)] text-[16px] font-medium leading-[1.45] tracking-[-0.3px] text-[#626262] sm:text-[18px]">
          Credits are just work. Here&apos;s what each tier looks like as a real
          Slack thread with Misterr.
        </p>
      </div>

      <div className="grid w-full max-w-[1120px] grid-cols-1 gap-[24px] md:grid-cols-3">
        {/* Quick tasks */}
        <TierCard tier={TIERS[0]}>
          <SlackThread
            user={{ name: "Dana Lee", img: "/landing/avatars/dana.jpg" }}
            time="9:14 AM"
            userText={
              <>
                <Mention>@Misterr</Mention> summarize today&apos;s{" "}
                <Channel>#support</Channel> channel and tell me which tickets are
                still open
              </>
            }
            replyTime="9:14 AM"
            replyText={
              <>
                12 messages, 3 open tickets. Acme&apos;s billing ticket has been
                unanswered for 4h. Want me to escalate it?
              </>
            }
            reactions={[
              { emoji: "✅", count: 3 },
              { emoji: "👀", count: 1 },
            ]}
          />
        </TierCard>

        {/* Complex workflows */}
        <TierCard tier={TIERS[1]}>
          <SlackThread
            user={{ name: "Sam Ortiz", img: "/landing/avatars/sam.jpg" }}
            time="2:48 PM"
            userText={
              <>
                <Mention>@Misterr</Mention> the changelog says v2.3 but the docs
                still say v2.1 — update them and have it ready for review
              </>
            }
            replyTime="2:51 PM"
            replyText={
              <>
                Done. Updated 6 doc pages to v2.3. Preview ready for your review:{" "}
                <ThreadLink>docs-preview/v2.3</ThreadLink>
              </>
            }
            reactions={[{ emoji: "🚀", count: 2 }]}
          />
        </TierCard>

        {/* Full projects */}
        <TierCard tier={TIERS[2]}>
          <SlackThread
            user={{ name: "Priya Nair", img: "/landing/avatars/priya.jpg" }}
            time="11:02 AM"
            userText={
              <>
                <Mention>@Misterr</Mention> put together a competitive analysis
                for the board — us vs the 3 big players, as a shareable PDF
              </>
            }
            replyTime="11:39 AM"
            replyText={
              <>
                Done. 10-page PDF with feature matrix, pricing comparison, and
                positioning map.
                <FileChip name="competitive-analysis.pdf" meta="PDF · 10 pages" />
              </>
            }
            reactions={[
              { emoji: "🙌", count: 4 },
              { emoji: "🔥", count: 2 },
            ]}
          />
        </TierCard>
      </div>
    </section>
  );
}

function TierCard({
  tier,
  children,
}: {
  tier: (typeof TIERS)[number];
  children: ReactNode;
}) {
  return (
    <div
      className={`flex flex-col gap-[18px] rounded-[20px] border border-[#191919] ${tier.tint} p-[24px] shadow-[0px_4px_0px_0px_#626262]`}
    >
      <div className="flex flex-col gap-[6px]">
        <span className="font-[family-name:var(--font-inter)] text-[14px] font-semibold uppercase tracking-[0.5px] text-[#626262]">
          {tier.label}
        </span>
        <span className="font-[family-name:var(--font-lexend)] text-[30px] font-semibold leading-[1.05] tracking-[-1.2px] text-[#191919]">
          {fmt(tier.min)}–{fmt(tier.max)}
          <span className="ml-[6px] text-[15px] font-medium tracking-[-0.3px] text-[#626262]">
            credits
          </span>
        </span>
        <p className="font-[family-name:var(--font-inter)] text-[15px] font-medium leading-[1.4] tracking-[-0.2px] text-[#191919]">
          {tier.desc}
        </p>
      </div>
      {children}
    </div>
  );
}

function SlackThread({
  user,
  time,
  userText,
  replyTime,
  replyText,
  reactions,
}: {
  user: { name: string; img: string };
  time: string;
  userText: ReactNode;
  replyTime: string;
  replyText: ReactNode;
  reactions?: { emoji: string; count: number }[];
}) {
  return (
    <div className="flex flex-col gap-[14px] rounded-[14px] border border-[#e7e2db] bg-white p-[16px]">
      <SlackMessage
        avatar={
          <img
            src={user.img}
            alt={user.name}
            className="size-[36px] shrink-0 rounded-[8px] object-cover"
          />
        }
        name={user.name}
        time={time}
        body={userText}
      />
      <SlackMessage
        avatar={
          <img
            src="/landing/misterr-avatar.png"
            alt="Misterr"
            className="size-[36px] shrink-0 rounded-[8px] object-cover"
          />
        }
        name="Misterr"
        isApp
        time={replyTime}
        body={replyText}
        reactions={reactions}
      />
    </div>
  );
}

function SlackMessage({
  avatar,
  name,
  isApp = false,
  time,
  body,
  reactions,
}: {
  avatar: ReactNode;
  name: string;
  isApp?: boolean;
  time: string;
  body: ReactNode;
  reactions?: { emoji: string; count: number }[];
}) {
  return (
    <div className="flex gap-[10px]">
      {avatar}
      <div className="flex min-w-0 flex-col gap-[3px]">
        <div className="flex items-center gap-[6px]">
          <span className="font-[family-name:var(--font-inter)] text-[15px] font-semibold text-[#1d1c1d]">
            {name}
          </span>
          {isApp && (
            <span className="rounded-[3px] bg-[#e8e8e8] px-[4px] py-[1px] font-[family-name:var(--font-inter)] text-[10px] font-semibold uppercase leading-[12px] tracking-[0.3px] text-[#616061]">
              App
            </span>
          )}
          <span className="font-[family-name:var(--font-inter)] text-[12px] text-[#9a9a9a]">
            {time}
          </span>
        </div>
        <p className="font-[family-name:var(--font-inter)] text-[14px] leading-[1.5] text-[#1d1c1d]">
          {body}
        </p>
        {reactions && reactions.length > 0 && (
          <div className="mt-[6px] flex flex-wrap gap-[6px]">
            {reactions.map((r) => (
              <span
                key={r.emoji}
                className="flex items-center gap-[4px] rounded-full border border-[#e1dfde] bg-[#f8f8f8] px-[8px] py-[2px] font-[family-name:var(--font-inter)] text-[12px] font-medium text-[#616061]"
              >
                <span>{r.emoji}</span>
                <span>{r.count}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Mention({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-[3px] bg-[#e8f1ff] px-[3px] font-medium text-[#1264a3]">
      {children}
    </span>
  );
}

function Channel({ children }: { children: ReactNode }) {
  return <span className="font-medium text-[#1264a3]">{children}</span>;
}

function ThreadLink({ children }: { children: ReactNode }) {
  return (
    <a href="#" className="font-medium text-[#1264a3] hover:underline">
      {children}
    </a>
  );
}

function FileChip({ name, meta }: { name: string; meta: string }) {
  return (
    <span className="mt-[8px] flex w-fit items-center gap-[10px] rounded-[10px] border border-[#e1dfde] bg-[#f8f8f8] px-[12px] py-[8px]">
      <span className="flex size-[28px] shrink-0 items-center justify-center rounded-[6px] bg-[#ff5200] font-[family-name:var(--font-inter)] text-[10px] font-bold text-white">
        PDF
      </span>
      <span className="flex flex-col">
        <span className="font-[family-name:var(--font-inter)] text-[13px] font-semibold text-[#1d1c1d]">
          {name}
        </span>
        <span className="font-[family-name:var(--font-inter)] text-[11px] text-[#9a9a9a]">
          {meta}
        </span>
      </span>
    </span>
  );
}
