"use client";

import { useState } from "react";

const TEAMS = [
  "Founders & CEOs",
  "Marketing & Growth",
  "Engineering",
  "Operations",
  "Finance",
  "Recruiting",
  "Sales",
  "Other",
];

export default function TeamTabs() {
  const [active, setActive] = useState("Engineering");
  return (
    <div className="flex w-full max-w-[900px] flex-wrap items-center justify-center gap-[8px] rounded-[999px] px-[12px] py-[4px]">
      {TEAMS.map((t) => {
        const isActive = t === active;
        return (
          <button
            key={t}
            type="button"
            onClick={() => setActive(t)}
            className={`flex h-[40px] items-center justify-center gap-[8px] overflow-clip rounded-[200px] transition-colors ${
              isActive
                ? "bg-[#222] px-[24px] py-[10px]"
                : "px-[16px] py-[8px] hover:bg-[#e7e2dc]"
            }`}
          >
            <p
              className={`font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[16px] whitespace-nowrap ${
                isActive ? "text-white" : "text-[#222]"
              }`}
            >
              {t}
            </p>
          </button>
        );
      })}
    </div>
  );
}
