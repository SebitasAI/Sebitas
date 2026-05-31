"use client";

import { useRef, useState } from "react";
import type { ReactNode } from "react";

// TODO use cases: each card is a public capability claim. Only keep teams and use cases
// Misterr does well TODAY. Move anything aspirational to a roadmap, not this page.
// If a team can't fill 3 distinct, strong use cases, cut the tab rather than padding it.

type UseCase = {
  title: string;
  description: string;
  req: string; // the Slack request to @Misterr
  res: string; // Misterr's reply
  chip?: string; // optional produced-artifact peek (file name)
};

// All tabs + their cards live here (team -> use cases). No duplicates across teams.
const TEAMS: Record<string, UseCase[]> = {
  "Founders & CEOs": [
    {
      title: "Investor updates, drafted",
      description:
        "Misterr pulls your metrics and recent activity into a monthly investor update, ready for your edits.",
      req: "@Misterr draft this month's investor update from our metrics and recent activity",
      res: "Done. Drafted with MRR, runway, and shipped highlights, ready for your edits.",
      chip: "investor-update-may.pdf",
    },
    {
      title: "Morning company digest",
      description:
        "A daily brief of what shipped, what's blocked, and what needs your decision.",
      req: "@Misterr give me today's company digest",
      res: "Shipped: 4 PRs. Blocked: billing migration. Needs you: approve the Acme contract.",
    },
    {
      title: "Market & competitor briefs",
      description:
        "Ask for a rundown on a competitor or market and get a board-ready brief, not a pile of links.",
      req: "@Misterr brief me on Competitor X before the board call",
      res: "Done. 2-page brief: positioning, pricing, and where we win.",
      chip: "competitor-x-brief.pdf",
    },
  ],
  "Marketing & Growth": [
    {
      title: "Campaign recaps",
      description:
        "Misterr pulls performance from your ad and analytics tools into a recap with clear takeaways.",
      req: "@Misterr recap last week's ad performance",
      res: "Spend, CAC, and top creatives pulled in. Takeaway: pause the LinkedIn set, scale TikTok.",
      chip: "campaign-recap.pdf",
    },
    {
      title: "Launch content, repurposed",
      description: "Turn one launch into draft blog, social, and email copy.",
      req: "@Misterr turn the v2.3 launch into blog, social, and email drafts",
      res: "Done. Blog draft, 4 social posts, and a launch email, all in your voice.",
    },
    {
      title: "Competitor watch",
      description:
        "Misterr tracks competitor pages and pricing changes and flags what moved.",
      req: "@Misterr anything change on competitors' pricing pages this week?",
      res: "Yes. Competitor Y dropped their Pro tier 20% and added a free trial. Flagged for you.",
    },
  ],
  Engineering: [
    {
      title: "Intelligent bug triage",
      description:
        "New issues get labeled, summarized, and routed to the right owner automatically.",
      req: "@Misterr triage the new issues in #bugs",
      res: "Labeled 7 issues, summarized each, and routed them to the right owners.",
    },
    {
      title: "PR review summaries",
      description:
        "Misterr summarizes a pull request, flags risky changes, and drafts review notes.",
      req: "@Misterr summarize PR #482 and flag anything risky",
      res: "Touches auth + migrations. 1 risky change in token expiry. Review notes drafted.",
    },
    {
      title: "Docs that stay in sync",
      description:
        "After a release, Misterr updates the docs to match and sends a preview to review.",
      req: "@Misterr docs still say v2.1, update to v2.3 and send a preview",
      res: "Done. Updated 6 doc pages. Preview ready for your review.",
      chip: "docs-preview/v2.3",
    },
    {
      title: "Incident + error response",
      description:
        "When an alert fires, Misterr summarizes it, drafts the incident note, and pings the on-call owner.",
      req: "@Misterr a 500 alert just fired in #alerts",
      res: "Summarized the trace, drafted the incident note, and paged the on-call owner.",
    },
  ],
  Operations: [
    {
      title: "Vendor & renewal tracking",
      description:
        "Misterr tracks contract renewals and flags what's expiring before it lapses.",
      req: "@Misterr what contracts renew next month?",
      res: "3 renewals: Datadog (auto-renews in 12d), Notion, Zoom. Flagged Datadog to review.",
    },
    {
      title: "Recurring requests, automated",
      description:
        "Turn a request you keep getting in Slack into a documented, repeatable workflow.",
      req: "@Misterr people keep asking for prod access in #it, make this repeatable",
      res: "Built a documented workflow: request, approver, grant, log. Want it live?",
    },
    {
      title: "Weekly ops report",
      description:
        "A status report pulled from across your tools, delivered every week without anyone building it.",
      req: "@Misterr post the weekly ops report",
      res: "Done. Tickets, uptime, and vendor status pulled from across tools.",
      chip: "ops-report.pdf",
    },
  ],
  Finance: [
    {
      title: "Expense triage",
      description:
        "Misterr categorizes expenses, flags anomalies, and chases missing receipts.",
      req: "@Misterr triage this week's expenses",
      res: "Categorized 42 expenses, flagged 2 anomalies, and chased 3 missing receipts.",
    },
    {
      title: "Burn & runway snapshots",
      description: "A weekly burn and runway summary posted right in Slack.",
      req: "@Misterr what's our burn and runway?",
      res: "Burn $214k/mo, runway 18 months. Posted the breakdown below.",
    },
    {
      title: "Invoice follow-ups",
      description:
        "Misterr drafts reminders for overdue invoices for you to approve and send.",
      req: "@Misterr any overdue invoices?",
      res: "4 overdue. Reminder drafts ready for your approval.",
    },
  ],
  Recruiting: [
    {
      title: "Candidate screening",
      description:
        "Misterr summarizes applications against the role and surfaces a shortlist.",
      req: "@Misterr screen the new applicants for the Staff Eng role",
      res: "Reviewed 28 applications against the role. Shortlist of 5 below.",
      chip: "shortlist.pdf",
    },
    {
      title: "Interview scheduling",
      description:
        "It coordinates across calendars and sends invites once you approve.",
      req: "@Misterr schedule onsites for the shortlist",
      res: "Coordinated calendars. Invites ready to send once you approve.",
    },
    {
      title: "Pipeline digest",
      description: "A weekly snapshot of where every candidate stands.",
      req: "@Misterr where does the hiring pipeline stand?",
      res: "Snapshot: 5 screening, 3 onsite, 1 offer out. Details below.",
    },
  ],
  Sales: [
    {
      title: "CRM that stays clean",
      description:
        "Misterr keeps your CRM updated from Slack and email and flags stale deals.",
      req: "@Misterr update the CRM from this week's threads",
      res: "Synced 18 deals from Slack and email. Flagged 4 stale deals, no activity in 14d.",
    },
    {
      title: "Call recaps & follow-ups",
      description: "Summarize a call, log next steps, and draft the follow-up email.",
      req: "@Misterr recap the Acme call and draft a follow-up",
      res: "Summary logged, next steps captured, follow-up email drafted for your review.",
    },
    {
      title: "Pipeline review",
      description:
        "A weekly digest highlighting at-risk deals and what needs attention.",
      req: "@Misterr run the weekly pipeline review",
      res: "Digest ready: 3 at-risk deals, 2 slipping close dates. Details below.",
      chip: "pipeline-review.pdf",
    },
  ],
  Other: [
    {
      title: "If you can describe it, Misterr can do it",
      description:
        "Connect any tool in your stack, describe the job in plain English, and Misterr figures out the steps.",
      req: "@Misterr pull churned accounts from Stripe and start a win-back in HubSpot",
      res: "Connected Stripe + HubSpot, figured out the steps, and queued the win-back. Approve to run?",
    },
  ],
};

const TAB_NAMES = Object.keys(TEAMS);
const AVATARS = [
  { name: "Dana Lee", img: "/landing/avatars/dana.jpg" },
  { name: "Sam Ortiz", img: "/landing/avatars/sam.jpg" },
  { name: "Priya Nair", img: "/landing/avatars/priya.jpg" },
];
const TIMES = ["9:14 AM", "11:02 AM", "2:48 PM", "4:36 PM"];

export default function UseCases() {
  const [active, setActive] = useState("Engineering");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (e: React.KeyboardEvent, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    const next = (i + dir + TAB_NAMES.length) % TAB_NAMES.length;
    setActive(TAB_NAMES[next]);
    tabRefs.current[next]?.focus();
  };

  const cards = TEAMS[active];

  return (
    <div className="flex w-full flex-col items-center gap-[40px]">
      {/* tabs */}
      <div
        role="tablist"
        aria-label="Use cases by team"
        className="flex w-full max-w-[900px] flex-wrap items-center justify-center gap-[8px]"
      >
        {TAB_NAMES.map((name, i) => {
          const isActive = name === active;
          return (
            <button
              key={name}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              role="tab"
              type="button"
              aria-selected={isActive}
              tabIndex={isActive ? 0 : -1}
              onClick={() => setActive(name)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={`flex h-[40px] items-center justify-center gap-[8px] overflow-clip rounded-[200px] transition-colors ${
                isActive
                  ? "bg-[#222] px-[24px] py-[10px]"
                  : "px-[16px] py-[8px] hover:bg-[#e7e2dc]"
              }`}
            >
              <span
                className={`font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[16px] whitespace-nowrap ${
                  isActive ? "text-white" : "text-[#222]"
                }`}
              >
                {name}
              </span>
            </button>
          );
        })}
      </div>

      {/* panel */}
      <div
        key={active}
        role="tabpanel"
        className="uc-fade grid w-full max-w-[1164px] grid-cols-1 gap-[24px] md:grid-cols-3"
      >
        {cards.map((c, i) => (
          <UseCaseCard key={c.title} card={c} index={i} />
        ))}
      </div>

      <div className="flex h-[40px] items-center justify-center gap-[8px] rounded-[200px] px-[16px] py-[8px]">
        <p className="font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[16px] text-[#222]">
          And much more
        </p>
      </div>
    </div>
  );
}

function UseCaseCard({ card, index }: { card: UseCase; index: number }) {
  const user = AVATARS[index % AVATARS.length];
  const time = TIMES[index % TIMES.length];
  const rest = card.req.replace(/^@Misterr\s*/, "");

  return (
    <div className="flex flex-col gap-[18px] rounded-[16px] border border-black bg-white p-[20px] shadow-[0px_4px_0px_0px_#626262]">
      {/* slack visual */}
      <div className="flex flex-col gap-[14px] rounded-[14px] border border-[#e7e2db] bg-white p-[16px] shadow-[0px_2px_8px_rgba(0,0,0,0.06)]">
        <SlackMessage
          avatar={
            <img
              src={user.img}
              alt={user.name}
              className="size-[34px] shrink-0 rounded-[8px] object-cover"
            />
          }
          name={user.name}
          time={time}
          body={
            <>
              <Mention>@Misterr</Mention> {rest}
            </>
          }
        />
        <SlackMessage
          avatar={
            <img
              src="/landing/misterr-avatar.png"
              alt="Misterr"
              className="size-[34px] shrink-0 rounded-[8px] object-cover"
            />
          }
          name="Misterr"
          isApp
          time={time}
          body={card.res}
        />
        {card.chip && <FileChip name={card.chip} />}
      </div>

      {/* copy */}
      <div className="flex flex-col gap-[6px]">
        <p className="font-[family-name:var(--font-inter)] text-[18px] font-semibold leading-[1.2] tracking-[-0.5px] text-[#191919]">
          {card.title}
        </p>
        <p className="font-[family-name:var(--font-inter)] text-[15px] font-medium leading-[1.45] tracking-[-0.2px] text-[#3a4250]">
          {card.description}
        </p>
      </div>
    </div>
  );
}

function SlackMessage({
  avatar,
  name,
  isApp = false,
  time,
  body,
}: {
  avatar: ReactNode;
  name: string;
  isApp?: boolean;
  time: string;
  body: ReactNode;
}) {
  return (
    <div className="flex gap-[10px]">
      {avatar}
      <div className="flex min-w-0 flex-col gap-[3px]">
        <div className="flex items-center gap-[6px]">
          <span className="font-[family-name:var(--font-inter)] text-[14px] font-semibold text-[#1d1c1d]">
            {name}
          </span>
          {isApp && (
            <span className="rounded-[3px] bg-[#e8e8e8] px-[4px] py-[1px] font-[family-name:var(--font-inter)] text-[9px] font-semibold uppercase leading-[12px] tracking-[0.3px] text-[#616061]">
              App
            </span>
          )}
          <span className="font-[family-name:var(--font-inter)] text-[11px] text-[#9a9a9a]">
            {time}
          </span>
        </div>
        <p className="font-[family-name:var(--font-inter)] text-[13.5px] leading-[1.5] text-[#1d1c1d]">
          {body}
        </p>
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

function FileChip({ name }: { name: string }) {
  const isPdf = name.toLowerCase().endsWith(".pdf");
  return (
    <span className="flex w-fit items-center gap-[8px] rounded-[10px] border border-[#e1dfde] bg-[#f8f8f8] px-[10px] py-[7px]">
      <span
        className={`flex size-[24px] shrink-0 items-center justify-center rounded-[6px] font-[family-name:var(--font-inter)] text-[9px] font-bold text-white ${
          isPdf ? "bg-[#ff5200]" : "bg-[#3f6ea5]"
        }`}
      >
        {isPdf ? "PDF" : "↗"}
      </span>
      <span className="font-[family-name:var(--font-inter)] text-[12px] font-semibold text-[#1d1c1d]">
        {name}
      </span>
    </span>
  );
}
