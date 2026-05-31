import type { ReactNode } from "react";

// Each card proves the hero's claim: a real Slack request and the finished
// thing Misterr hands back, shown as a contained Slack file/link attachment.
type Attachment =
  | { kind: "file"; tile: "pdf" | "hubspot"; name: string; meta: string }
  | { kind: "link"; label: string };

type Card = {
  label: string;
  headline: string;
  description: string;
  user: { name: string; img: string };
  time: string;
  userText: ReactNode;
  replyTime: string;
  replyText: ReactNode;
  attachment: Attachment;
  reaction?: { emoji: string; count: number };
};

const CARDS: Card[] = [
  {
    label: "Support & CRM",
    headline: "Nothing slips through the cracks",
    description:
      "Ask Misterr to triage a channel and it summarizes the thread, flags what needs a reply, and drafts the CRM follow-up, ready to send.",
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
    attachment: {
      kind: "file",
      tile: "hubspot",
      name: "Acme follow-up",
      meta: "HubSpot draft",
    },
    reaction: { emoji: "✅", count: 3 },
  },
  {
    label: "Docs & engineering",
    headline: "Docs that keep themselves current",
    description:
      "Point Misterr at a release and it updates the docs to match, then hands you a preview link to review before anything goes live.",
    user: { name: "Sam Ortiz", img: "/landing/avatars/sam.jpg" },
    time: "2:48 PM",
    userText: (
      <>
        <Mention>@Misterr</Mention> the changelog says v2.3 but the docs still
        say v2.1, update them and have it ready for review
      </>
    ),
    replyTime: "2:51 PM",
    replyText: <>Done. Updated 6 doc pages to v2.3. Preview ready for your review:</>,
    attachment: { kind: "link", label: "docs-preview/v2.3" },
    reaction: { emoji: "🚀", count: 2 },
  },
  {
    label: "Research & deliverables",
    headline: "A finished deliverable, not a chat reply",
    description:
      "Give Misterr a brief and it comes back with the actual file: a board-ready PDF with the work done, not a wall of text to copy-paste.",
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
    attachment: {
      kind: "file",
      tile: "pdf",
      name: "competitive-analysis-q1.pdf",
      meta: "10 pages",
    },
    reaction: { emoji: "🙌", count: 4 },
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
            className="flex flex-col gap-[18px] rounded-[16px] border border-[#bcd9ef] bg-[#ddf2ff] p-[20px]"
          >
            {/* 1-3: copy on top */}
            <div className="flex flex-col gap-[8px]">
              <span className="font-[family-name:var(--font-inter)] text-[13px] font-semibold uppercase tracking-[0.5px] text-[#3f6ea5]">
                {c.label}
              </span>
              <p className="font-[family-name:var(--font-inter)] text-[20px] font-semibold leading-[1.2] tracking-[-0.6px] text-[#191919]">
                {c.headline}
              </p>
              <p className="font-[family-name:var(--font-inter)] text-[15px] font-medium leading-[1.45] tracking-[-0.2px] text-[#3a4250]">
                {c.description}
              </p>
            </div>

            {/* 4: slack thread as proof, below */}
            <SlackThread {...c} />
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
  attachment,
  reaction,
}: Card) {
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
        attachment={attachment}
        reaction={reaction}
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
  attachment,
  reaction,
}: {
  avatar: ReactNode;
  name: string;
  isApp?: boolean;
  time: string;
  body: ReactNode;
  attachment?: Attachment;
  reaction?: { emoji: string; count: number };
}) {
  return (
    <div className="flex min-w-0 gap-[10px]">
      {avatar}
      <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
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
        {attachment && <AttachmentChip attachment={attachment} />}
        {reaction && (
          <div className="mt-[6px] flex">
            <span className="flex items-center gap-[4px] rounded-full border border-[#e1dfde] bg-[#f8f8f8] px-[8px] py-[2px] font-[family-name:var(--font-inter)] text-[12px] font-medium text-[#616061]">
              <span>{reaction.emoji}</span>
              <span>{reaction.count}</span>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function AttachmentChip({ attachment }: { attachment: Attachment }) {
  if (attachment.kind === "link") {
    return (
      <span className="mt-[6px] flex max-w-full items-center gap-[8px] self-start rounded-[10px] border border-[#e1dfde] bg-[#f8f8f8] px-[10px] py-[7px]">
        <span className="flex size-[22px] shrink-0 items-center justify-center rounded-[6px] bg-[#1264a3] text-white">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-[13px]">
            <path d="M9 15l6-6M10 6h5a3 3 0 0 1 0 6h-1M14 18H9a3 3 0 0 1 0-6h1" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="truncate font-[family-name:var(--font-inter)] text-[13px] font-medium text-[#1264a3]">
          {attachment.label}
        </span>
      </span>
    );
  }

  const isPdf = attachment.tile === "pdf";
  return (
    <span className="mt-[6px] flex max-w-full items-center gap-[10px] self-start rounded-[10px] border border-[#e1dfde] bg-[#f8f8f8] px-[10px] py-[8px]">
      <span
        className={`flex size-[28px] shrink-0 items-center justify-center rounded-[6px] font-[family-name:var(--font-inter)] text-[9px] font-bold text-white ${
          isPdf ? "bg-[#ff5200]" : "bg-[#ff7a59]"
        }`}
      >
        {isPdf ? "PDF" : "H"}
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-[family-name:var(--font-inter)] text-[13px] font-semibold text-[#1d1c1d]">
          {attachment.name}
        </span>
        <span className="truncate font-[family-name:var(--font-inter)] text-[11px] text-[#9a9a9a]">
          {attachment.meta}
        </span>
      </span>
    </span>
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
