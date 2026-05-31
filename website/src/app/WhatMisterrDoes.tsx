import type { ReactNode } from "react";

// Each card proves the hero's claim by showing a real Slack request and the
// finished thing Misterr hands back. Same example "voice" as the pricing
// "what your credits get done" section, but the emphasis here is the artifact.
const CARDS: {
  label: string;
  headline: string;
  body: string;
  user: { name: string; img: string };
  time: string;
  userText: ReactNode;
  replyTime: string;
  replyText: ReactNode;
  reactions?: { emoji: string; count: number }[];
  artifact: ReactNode;
}[] = [
  {
    label: "Support & CRM",
    headline: "Nothing slips through the cracks",
    body: "Ask Misterr to triage a channel and it summarizes the thread, flags what needs a reply, and drafts the CRM follow-up, ready to send.",
    user: { name: "Dana Lee", img: "/landing/avatars/dana.jpg" },
    time: "9:14 AM",
    userText: (
      <>
        <Mention>@Misterr</Mention> summarize today&apos;s{" "}
        <Channel>#support</Channel> channel and tell me what needs follow-up
      </>
    ),
    replyTime: "9:14 AM",
    replyText: (
      <>
        12 messages, 3 open tickets. Acme&apos;s billing ticket has been
        unanswered for 4h. I drafted a follow-up in HubSpot, want me to send it?
      </>
    ),
    reactions: [
      { emoji: "✅", count: 3 },
      { emoji: "🙏", count: 1 },
    ],
    artifact: <CrmArtifact />,
  },
  {
    label: "Docs & Engineering",
    headline: "Docs that keep themselves current",
    body: "Point Misterr at a release and it updates the docs to match, then hands you a preview link to review before anything goes live.",
    user: { name: "Sam Ortiz", img: "/landing/avatars/sam.jpg" },
    time: "2:48 PM",
    userText: (
      <>
        <Mention>@Misterr</Mention> the changelog says v2.3 but the docs still
        say v2.1, update them and have it ready for review
      </>
    ),
    replyTime: "2:51 PM",
    replyText: (
      <>
        Done. Updated 6 doc pages to v2.3. Preview ready for your review:{" "}
        <ThreadLink>docs-preview/v2.3</ThreadLink>
      </>
    ),
    reactions: [{ emoji: "🚀", count: 2 }],
    artifact: <DocArtifact />,
  },
  {
    label: "Research & Deliverables",
    headline: "A finished deliverable, not a chat reply",
    body: "Give Misterr a brief and it comes back with the actual file: a board-ready PDF with the work done, not a wall of text to copy-paste.",
    user: { name: "Priya Nair", img: "/landing/avatars/priya.jpg" },
    time: "11:02 AM",
    userText: (
      <>
        <Mention>@Misterr</Mention> put together a competitive analysis for the
        board, us vs the 3 big players, as a shareable PDF
      </>
    ),
    replyTime: "11:39 AM",
    replyText: (
      <>
        Done. 10-page PDF with feature matrix, pricing comparison, and
        positioning map.
      </>
    ),
    reactions: [
      { emoji: "🙌", count: 4 },
      { emoji: "🔥", count: 2 },
    ],
    artifact: <PdfArtifact />,
  },
];

export default function WhatMisterrDoes() {
  return (
    <section className="flex w-full flex-col items-center gap-[40px] overflow-clip bg-[#faf5f1] px-[24px] pb-[64px] pt-[40px] sm:px-[60px] sm:pb-[80px]">
      <p className="min-w-full font-[family-name:var(--font-lexend)] text-[30px] font-semibold tracking-[-1.5px] text-center text-[#191919] [word-break:break-word] sm:text-[48px] sm:tracking-[-2.4px]">
        Misterr ships real work without leaving Slack
      </p>

      <div className="grid w-full max-w-[1164px] grid-cols-1 gap-[24px] md:grid-cols-3">
        {CARDS.map((c) => (
          <div
            key={c.label}
            className="flex flex-col gap-[20px] rounded-[16px] border border-[#191919] bg-[#ddf2ff] p-[20px] shadow-[0px_4px_0px_0px_#626262]"
          >
            {/* visual: slack thread + artifact peek */}
            <div className="relative pb-[28px] pt-[14px]">
              {c.artifact}
              <div className="relative z-10">
                <SlackThread {...c} />
              </div>
            </div>

            {/* copy */}
            <div className="flex flex-col gap-[8px]">
              <span className="font-[family-name:var(--font-inter)] text-[13px] font-semibold uppercase tracking-[0.5px] text-[#3f6ea5]">
                {c.label}
              </span>
              <p className="font-[family-name:var(--font-inter)] text-[20px] font-semibold leading-[1.2] tracking-[-0.6px] text-[#191919]">
                {c.headline}
              </p>
              <p className="font-[family-name:var(--font-inter)] text-[15px] font-medium leading-[1.45] tracking-[-0.2px] text-[#3a4250]">
                {c.body}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
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
    <div className="relative flex flex-col gap-[14px] rounded-[14px] border border-[#e7e2db] bg-white p-[16px] shadow-[0px_2px_8px_rgba(0,0,0,0.06)]">
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

/* ── artifact peeks (the finished output behind/under the thread) ─────────── */

function CrmArtifact() {
  return (
    <div className="absolute bottom-0 left-[8px] right-[8px] z-0 flex items-center gap-[10px] rounded-[12px] border border-[#e1dfde] bg-white px-[12px] py-[10px] shadow-[0px_6px_16px_rgba(0,0,0,0.08)]">
      <span className="flex size-[26px] shrink-0 items-center justify-center rounded-[6px] bg-[#ff7a59] font-[family-name:var(--font-inter)] text-[12px] font-bold text-white">
        H
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-[family-name:var(--font-inter)] text-[12px] font-semibold text-[#1d1c1d]">
          Acme Inc — Billing follow-up
        </span>
        <span className="font-[family-name:var(--font-inter)] text-[11px] text-[#9a9a9a]">
          HubSpot · Draft ready to send
        </span>
      </span>
    </div>
  );
}

function DocArtifact() {
  return (
    <div className="absolute right-0 top-0 z-0 hidden w-[150px] rotate-[3deg] flex-col gap-[6px] rounded-[8px] border border-[#e1dfde] bg-white p-[12px] shadow-[0px_8px_20px_rgba(0,0,0,0.10)] sm:flex">
      <div className="flex items-center justify-between">
        <span className="font-[family-name:var(--font-inter)] text-[10px] font-semibold text-[#1d1c1d]">
          Docs · v2.3
        </span>
        <span className="rounded-[3px] bg-[#e6f4ea] px-[4px] py-[1px] font-[family-name:var(--font-inter)] text-[8px] font-semibold uppercase text-[#1a7f37]">
          Preview
        </span>
      </div>
      <div className="h-[5px] w-full rounded-full bg-[#ececec]" />
      <div className="h-[5px] w-[80%] rounded-full bg-[#ececec]" />
      <div className="h-[5px] w-[90%] rounded-full bg-[#ececec]" />
      <div className="h-[5px] w-[60%] rounded-full bg-[#ececec]" />
    </div>
  );
}

function PdfArtifact() {
  return (
    <div className="absolute bottom-0 left-[8px] right-[8px] z-0 flex items-center gap-[10px] rounded-[12px] border border-[#e1dfde] bg-white px-[12px] py-[10px] shadow-[0px_6px_16px_rgba(0,0,0,0.08)]">
      <span className="flex size-[28px] shrink-0 items-center justify-center rounded-[6px] bg-[#ff5200] font-[family-name:var(--font-inter)] text-[10px] font-bold text-white">
        PDF
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-[family-name:var(--font-inter)] text-[12px] font-semibold text-[#1d1c1d]">
          competitive-analysis.pdf
        </span>
        <span className="font-[family-name:var(--font-inter)] text-[11px] text-[#9a9a9a]">
          10 pages · feature matrix, pricing, positioning
        </span>
      </span>
    </div>
  );
}
