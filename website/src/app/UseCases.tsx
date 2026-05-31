"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import type { ReactNode } from "react";

// TODO use cases: each feature is a public capability claim: only keep what Misterr does well TODAY.
// Finance especially: never imply autonomous money movement; keep it draft/flag/chase + approval.
// If a tab can't hold 4 distinct, true features, fold it into Other rather than padding it.

type Feature = { icon: IconName; title: string; description: string };
type Team = { icon: IconName; summary: string; features: Feature[] };

// All tabs + content in one config object (team -> { summary, features[4] }).
const TEAMS: Record<string, Team> = {
  "Founders & CEOs": {
    icon: "crown",
    summary:
      "Misterr keeps you on top of the company. It drafts your updates, briefs your decisions, and surfaces what needs you.",
    features: [
      {
        icon: "doc",
        title: "Investor updates, drafted",
        description:
          "Pulls MRR, runway, and recent activity into a monthly investor update, ready for your edits.",
      },
      {
        icon: "sun",
        title: "Morning company digest",
        description:
          "A daily brief of what shipped, what's blocked, and the decisions waiting on you.",
      },
      {
        icon: "search",
        title: "Market & competitor briefs",
        description:
          "Ask about a competitor or market and get a board-ready brief, not a pile of links.",
      },
      {
        icon: "inbox",
        title: "Inbox & meeting triage",
        description:
          "Surfaces the messages and meetings that actually need you and drafts the replies.",
      },
    ],
  },
  "Marketing & Growth": {
    icon: "megaphone",
    summary:
      "Misterr runs the busywork behind growth: recapping campaigns, drafting content, and watching the competition.",
    features: [
      {
        icon: "chart",
        title: "Campaign recaps",
        description:
          "Pulls performance from your ad and analytics tools into a recap with clear takeaways.",
      },
      {
        icon: "pen",
        title: "Content, drafted & repurposed",
        description:
          "Turns one launch into blog, social, and email drafts in your voice.",
      },
      {
        icon: "eye",
        title: "Competitor watch",
        description:
          "Tracks competitor pages and pricing changes and flags what moved.",
      },
      {
        icon: "calendar",
        title: "Content calendar, kept current",
        description:
          "Updates the calendar and nudges owners before things slip.",
      },
    ],
  },
  Operations: {
    icon: "gear",
    summary:
      "Misterr handles the recurring operational work no one wants to own: tracking, reporting, and chasing, so nothing slips.",
    features: [
      {
        icon: "refresh",
        title: "Vendor & renewal tracking",
        description:
          "Tracks contract renewals and flags what's expiring before it lapses.",
      },
      {
        icon: "bolt",
        title: "Recurring requests, automated",
        description:
          "Turns a request you keep getting in Slack into a documented, repeatable workflow.",
      },
      {
        icon: "clipboard",
        title: "Weekly ops reports",
        description:
          "A status report pulled from across your tools, delivered on schedule.",
      },
      {
        icon: "users",
        title: "Onboarding & offboarding",
        description:
          "Kicks off and tracks every step when someone joins or leaves.",
      },
    ],
  },
  Finance: {
    icon: "dollar",
    summary:
      "Misterr keeps the numbers tidy: categorizing, flagging, and chasing. And it always asks before anything touches money.",
    features: [
      {
        icon: "tag",
        title: "Expense triage",
        description:
          "Categorizes expenses, flags anomalies, and chases missing receipts.",
      },
      {
        icon: "trending",
        title: "Burn & runway snapshots",
        description: "A weekly burn and runway summary posted right in Slack.",
      },
      {
        icon: "mail",
        title: "Invoice follow-ups",
        description:
          "Drafts reminders for overdue invoices for you to approve and send.",
      },
      {
        icon: "docCheck",
        title: "Month-end close prep",
        description:
          "Assembles the numbers and a draft summary to speed up close.",
      },
    ],
  },
  Sales: {
    icon: "funnel",
    summary:
      "Misterr keeps your pipeline clean and moving: logging, recapping, and following up, so deals don't go cold.",
    features: [
      {
        icon: "checkCircle",
        title: "CRM that stays clean",
        description:
          "Keeps HubSpot or Salesforce updated from Slack and email and flags stale deals.",
      },
      {
        icon: "phone",
        title: "Call recaps & follow-ups",
        description:
          "Summarizes a call, logs next steps, and drafts the follow-up email.",
      },
      {
        icon: "funnel",
        title: "Pipeline reviews",
        description:
          "A weekly digest highlighting at-risk deals and what needs attention.",
      },
      {
        icon: "doc",
        title: "Proposals & quotes, drafted",
        description:
          "Drafts a first-version proposal or quote from the deal context.",
      },
    ],
  },
  Other: {
    icon: "grid",
    summary:
      "If a team works in Slack, Misterr works alongside them. A few of the rest:",
    features: [
      {
        icon: "code",
        title: "Engineering",
        description:
          "Triages bugs, summarizes PRs, and keeps docs in sync after releases.",
      },
      {
        icon: "bell",
        title: "Incident response",
        description:
          "When an alert fires, summarizes it, drafts the incident note, and pings the on-call owner.",
      },
      {
        icon: "userPlus",
        title: "Recruiting",
        description:
          "Screens applications against the role, shortlists, and schedules interviews once you approve.",
      },
      {
        icon: "sparkles",
        title: "Anything you can describe",
        description:
          "Connect any tool, describe the job in plain English, and Misterr figures out the steps.",
      },
    ],
  },
};

const TAB_NAMES = Object.keys(TEAMS);

export default function UseCases() {
  const [active, setActive] = useState("Founders & CEOs");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (i: number, dir: number) => {
    const next = (i + dir + TAB_NAMES.length) % TAB_NAMES.length;
    setActive(TAB_NAMES[next]);
    tabRefs.current[next]?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent, i: number) => {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      move(i, 1);
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      move(i, -1);
    }
  };

  const team = TEAMS[active];

  return (
    <div className="flex w-full max-w-[1164px] flex-col gap-[40px] lg:flex-row lg:gap-[64px]">
      {/* left column */}
      <div className="flex w-full flex-col gap-[28px] lg:w-[380px] lg:shrink-0">
        <div className="flex flex-col gap-[12px]">
          <span className="font-[family-name:var(--font-inter)] text-[14px] font-semibold uppercase tracking-[1px] text-[#ff5200]">
            Use cases
          </span>
          <h2 className="font-[family-name:var(--font-lexend)] text-[34px] font-semibold leading-[1.05] tracking-[-1.6px] text-[#191919] sm:text-[42px] sm:tracking-[-2.1px]">
            What Misterr can own for your team.
          </h2>
        </div>

        <div
          role="tablist"
          aria-orientation="vertical"
          aria-label="Use cases by team"
          className="flex flex-col gap-[4px]"
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
                id={`uc-tab-${i}`}
                aria-selected={isActive}
                aria-controls="uc-panel"
                tabIndex={isActive ? 0 : -1}
                onClick={() => setActive(name)}
                onKeyDown={(e) => onKeyDown(e, i)}
                className={`flex items-center gap-[12px] rounded-[12px] px-[16px] py-[12px] text-left transition-colors ${
                  isActive
                    ? "bg-[#191919] text-white"
                    : "text-[#191919] hover:bg-[#ece7e1]"
                }`}
              >
                <Icon
                  name={TEAMS[name].icon}
                  className={`size-[18px] shrink-0 ${
                    isActive ? "text-white" : "text-[#626262]"
                  }`}
                />
                <span className="font-[family-name:var(--font-inter)] text-[17px] font-medium tracking-[-0.3px]">
                  {name}
                </span>
              </button>
            );
          })}
        </div>

        <Link
          href="/signup"
          className="flex h-[48px] w-fit items-center justify-center rounded-[12px] bg-[#ff5200] px-[28px] font-[family-name:var(--font-geist)] text-[16px] font-semibold text-white transition-colors hover:bg-[#ff6a23]"
        >
          Start for free
        </Link>
      </div>

      {/* right column */}
      <div
        key={active}
        role="tabpanel"
        id="uc-panel"
        aria-labelledby={`uc-tab-${TAB_NAMES.indexOf(active)}`}
        className="uc-fade flex flex-1 flex-col gap-[28px] rounded-[20px] border border-[#191919] bg-white p-[28px] shadow-[0px_4px_0px_0px_#626262] sm:p-[36px]"
      >
        <p className="font-[family-name:var(--font-lexend)] text-[20px] font-semibold leading-[1.35] tracking-[-0.6px] text-[#191919] sm:text-[24px]">
          {team.summary}
        </p>

        <div className="grid grid-cols-1 gap-[24px] sm:grid-cols-2">
          {team.features.map((f) => (
            <div key={f.title} className="flex flex-col gap-[10px]">
              <span className="flex size-[40px] items-center justify-center rounded-[10px] bg-[#ddf2ff]">
                <Icon name={f.icon} className="size-[20px] text-[#191919]" />
              </span>
              <p className="font-[family-name:var(--font-inter)] text-[16px] font-semibold tracking-[-0.3px] text-[#191919]">
                {f.title}
              </p>
              <p className="font-[family-name:var(--font-inter)] text-[14px] font-medium leading-[1.45] tracking-[-0.2px] text-[#3a4250]">
                {f.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── hand-rolled line icons (stroke = currentColor), no icon library ──────── */
type IconName =
  | "crown"
  | "megaphone"
  | "gear"
  | "dollar"
  | "funnel"
  | "grid"
  | "doc"
  | "sun"
  | "search"
  | "inbox"
  | "chart"
  | "pen"
  | "eye"
  | "calendar"
  | "refresh"
  | "bolt"
  | "clipboard"
  | "users"
  | "tag"
  | "trending"
  | "mail"
  | "docCheck"
  | "checkCircle"
  | "phone"
  | "code"
  | "bell"
  | "userPlus"
  | "sparkles";

const PATHS: Record<IconName, ReactNode> = {
  crown: <path d="M4 18h16M4 18l-1-9 5 4 4-7 4 7 5-4-1 9" />,
  megaphone: <path d="M4 10v4l10 4V6L4 10zM4 10H3v4h1M8 15v4h3" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
    </>
  ),
  dollar: <path d="M12 2v20M16 6.5C16 5 14.5 4 12 4S8 5 8 7s2 3 4 3 4 1 4 3-1.5 3-4 3-4-1-4-2.5" />,
  funnel: <path d="M3 4h18l-7 8v7l-4 2v-9L3 4z" />,
  grid: (
    <>
      <rect x="4" y="4" width="6" height="6" rx="1" />
      <rect x="14" y="4" width="6" height="6" rx="1" />
      <rect x="4" y="14" width="6" height="6" rx="1" />
      <rect x="14" y="14" width="6" height="6" rx="1" />
    </>
  ),
  doc: <path d="M7 3h7l5 5v13H7V3zM14 3v5h5M10 13h6M10 17h6" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M16 16l4 4" />
    </>
  ),
  inbox: <path d="M3 5h18v14H3V5zM3 13h5l2 3h4l2-3h5" />,
  chart: <path d="M4 20V10M10 20V4M16 20v-8M22 20H2" />,
  pen: <path d="M4 20l4-1 11-11-3-3L5 16l-1 4zM14 5l3 3" />,
  eye: (
    <>
      <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="2.5" />
    </>
  ),
  calendar: <path d="M4 5h16v16H4V5zM4 9h16M8 3v4M16 3v4M8 14h3" />,
  refresh: <path d="M20 11a8 8 0 0 0-14-4M4 5v4h4M4 13a8 8 0 0 0 14 4M20 19v-4h-4" />,
  bolt: <path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z" />,
  clipboard: <path d="M8 4H6v16h12V4h-2M9 3h6v3H9V3zM9 11h6M9 15h6" />,
  users: (
    <>
      <circle cx="8" cy="9" r="3" />
      <circle cx="17" cy="10" r="2.5" />
      <path d="M3 20a5 5 0 0 1 10 0M14 20a4 4 0 0 1 7-2.5" />
    </>
  ),
  tag: (
    <>
      <path d="M3 3h8l10 10-8 8L3 11V3z" />
      <circle cx="7.5" cy="7.5" r="1.3" />
    </>
  ),
  trending: <path d="M3 17l6-6 4 4 8-8M21 7v5M21 7h-5" />,
  mail: <path d="M3 5h18v14H3V5zM3 6l9 7 9-7" />,
  docCheck: <path d="M7 3h7l5 5v13H7V3zM14 3v5h5M9 14l2 2 4-4" />,
  checkCircle: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12l3 3 5-6" />
    </>
  ),
  phone: <path d="M5 3h4l2 5-3 2a12 12 0 0 0 5 5l2-3 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2z" />,
  code: <path d="M9 8l-5 4 5 4M15 8l5 4-5 4" />,
  bell: <path d="M6 9a6 6 0 0 1 12 0c0 6 2 7 2 7H4s2-1 2-7zM10 20a2 2 0 0 0 4 0" />,
  userPlus: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3 20a6 6 0 0 1 12 0M18 8v6M15 11h6" />
    </>
  ),
  sparkles: <path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8L12 3zM18 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />,
};

function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {PATHS[name]}
    </svg>
  );
}
