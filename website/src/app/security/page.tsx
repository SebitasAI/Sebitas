import type { Metadata } from "next";
import Link from "next/link";

import Footer from "../Footer";
import Header from "../Header";
import { BOOK_SALES_URL } from "@/lib/app-url";


// Security overview page. Public-facing. Two ground rules:
//   1. Every claim here must be true today. No fabricated SOC 2 badges,
//      no "we run quarterly pen tests" if we don't. Marketing-grade
//      security copy turns into a legal liability the minute a customer
//      relies on it.
//   2. Concrete controls (Fernet at rest, OAuth, workspace isolation)
//      are spelled out so a security-conscious buyer can verify them
//      against our code. Aspirational items are clearly marked
//      "Roadmap" so we don't conflate present and future.
//
// All "Talk to security" buttons route to the same Calendar URL the
// rest of the site uses; we don't have a dedicated security inbox
// yet -- when we add one, swap the href here.

export const metadata: Metadata = {
  title: "Security at Misterr",
  description:
    "How Misterr handles your data, credentials, and integrations. Multi-tenant isolation, encrypted-at-rest tokens, OAuth-only auth, and what's on our security roadmap.",
};


type Control = {
  title: string;
  description: string;
};

const TENANCY_CONTROLS: Control[] = [
  {
    title: "Workspace isolation",
    description:
      "Every query that touches your data is scoped by your workspace ID at the database layer. No cross-tenant data path exists in the application code.",
  },
  {
    title: "Integration tokens stay per-workspace",
    description:
      "Your Salesforce / HubSpot / Metabase OAuth credentials never leave the workspace they were connected from. Misterr's agent runs with your tokens, not a shared service account.",
  },
  {
    title: "No cross-customer learning",
    description:
      "Your conversations, skills, and integration data are never used to train models, never shared across customers, and never sold.",
  },
];

const DATA_CONTROLS: Control[] = [
  {
    title: "Tokens encrypted at rest (Fernet)",
    description:
      "Slack bot tokens and integration credentials are encrypted with Fernet (AES-128-CBC + HMAC-SHA-256) before being written to the database. The encryption key is held in a managed secret store and rotated when needed.",
  },
  {
    title: "Database encryption (Neon)",
    description:
      "We use Neon's managed Postgres, which encrypts data at rest using AES-256 and traffic in transit using TLS 1.2+. Daily automated backups with point-in-time recovery.",
  },
  {
    title: "TLS in transit, everywhere",
    description:
      "Every request between your browser, Slack, our backend, and Anthropic is HTTPS / TLS. No plaintext links anywhere in the stack.",
  },
  {
    title: "No training on your data",
    description:
      "Anthropic's API (the LLM Misterr runs on) does not use your prompts or completions to train its models. Our infrastructure does not log full message bodies in plaintext beyond what's needed to deliver the reply.",
  },
];

const AUTH_CONTROLS: Control[] = [
  {
    title: "Slack OAuth for install",
    description:
      "Misterr is installed in your workspace through Slack's standard OAuth flow. We request only the scopes we need (read messages in channels we're added to, post replies, read user profiles for mentions). You can revoke them in your Slack admin at any time.",
  },
  {
    title: "Clerk for web app authentication",
    description:
      "Sign-in and session management for the misterr.ai web app are handled by Clerk. We don't store passwords. Clerk supports MFA out of the box; we'll surface workspace-level MFA enforcement on the Business plan.",
  },
  {
    title: "Per-integration OAuth",
    description:
      "Connecting Salesforce, HubSpot, Metabase, etc. uses each vendor's OAuth flow, so the credential you grant Misterr is scoped to the permissions you approve. Revoking access in the vendor's app immediately stops Misterr from reaching it.",
  },
];

const ROADMAP: Control[] = [
  {
    title: "SOC 2 Type II",
    description:
      "We're early; we don't have it yet. Customers on the Business / Enterprise tiers can request our security questionnaire and roadmap directly via the security contact below.",
  },
  {
    title: "RBAC + audit log",
    description:
      "Today every member of an installed workspace can talk to Misterr. We're building role-based controls (owner / admin / member) plus an immutable audit log on the Business tier.",
  },
  {
    title: "SSO + SAML",
    description:
      "On the roadmap for Business / Enterprise. Today the only auth path is Clerk + Slack OAuth.",
  },
  {
    title: "DPA + custom data retention",
    description:
      "Standard Data Processing Agreement and custom retention windows are available for Enterprise contracts on request.",
  },
];


export default function SecurityPage() {
  return (
    <main className="flex min-h-screen w-full flex-col items-center overflow-x-clip bg-white">
      <Header />

      {/* Hero */}
      <section className="flex w-full flex-col items-center gap-[18px] bg-gradient-to-b from-[#ddf2ff] to-white px-[24px] pb-[40px] pt-[160px] text-center">
        <span className="rounded-full bg-white/70 px-[14px] py-[6px] font-[family-name:var(--font-inter)] text-[12px] font-semibold uppercase tracking-[2px] text-[#4a4a4a] backdrop-blur">
          Security
        </span>
        <h1 className="max-w-[820px] font-[family-name:var(--font-lexend)] text-[40px] font-semibold leading-[1.05] tracking-[-2px] text-[#191919] sm:text-[56px] sm:tracking-[-2.8px]">
          Your data stays yours
        </h1>
        <p className="max-w-[640px] font-[family-name:var(--font-inter)] text-[18px] font-medium leading-[1.45] tracking-[-0.4px] text-[#626262]">
          Cómo manejamos tus credenciales, conversaciones y conexiones a otras
          herramientas. Específico, no marketing.
        </p>
        <div className="mt-[8px] flex flex-wrap items-center justify-center gap-[10px]">
          <a
            href={BOOK_SALES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-[12px] bg-[#191919] px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-white transition active:translate-y-[2px] hover:bg-black"
          >
            Talk to security
          </a>
          <a
            href="mailto:security@misterr.ai"
            className="rounded-[12px] border border-[#191919] bg-white px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-[#191919] transition hover:bg-[#191919] hover:text-white"
          >
            security@misterr.ai
          </a>
        </div>
      </section>

      <ControlsSection
        eyebrow="Tenancy"
        title="Multi-tenant from day one"
        description="Misterr was built workspace-first. There's no shared scratch space, no shared service account, no cross-tenant data path in the code."
        controls={TENANCY_CONTROLS}
      />

      <ControlsSection
        eyebrow="Data"
        title="What's encrypted, where it lives, who can see it"
        description="Tu data nunca entrena modelos, nunca se cruza con la de otros clientes, y nunca se almacena en plano cuando hay una clave encima."
        controls={DATA_CONTROLS}
        tint="bg-[#faf5f1]"
      />

      <ControlsSection
        eyebrow="Authentication"
        title="OAuth todo el camino"
        description="No guardamos passwords nunca. Cada acceso (Slack, web, integraciones) pasa por el OAuth del vendor correspondiente, y lo podés revocar desde ahí."
        controls={AUTH_CONTROLS}
      />

      <ControlsSection
        eyebrow="Roadmap"
        title="Lo que estamos construyendo"
        description="Marcamos lo que aún no está. Si tu equipo de security necesita algo de esta lista YA, hablalo con nosotros — para Enterprise lo aceleramos por contrato."
        controls={ROADMAP}
        tint="bg-[#faf5f1]"
      />

      {/* Subprocessors */}
      <section className="flex w-full flex-col items-center bg-white px-[24px] py-[60px]">
        <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-[24px]">
          <div className="flex flex-col gap-[8px]">
            <p className="font-[family-name:var(--font-inter)] text-[12px] font-semibold uppercase tracking-[2px] text-[#4a4a4a]">
              Subprocessors
            </p>
            <h2 className="font-[family-name:var(--font-lexend)] text-[28px] font-semibold tracking-[-1px] text-[#191919] sm:text-[36px]">
              Quién toca tu data, y para qué
            </h2>
          </div>
          <div className="overflow-hidden rounded-[16px] border border-[#191919] bg-white shadow-[0px_4px_0px_0px_#626262]">
            <table className="w-full font-[family-name:var(--font-inter)]">
              <thead className="border-b border-[#e1dfde] bg-[#faf5f1] text-[12px] uppercase tracking-[1px] text-[#4a4a4a]">
                <tr>
                  <th className="px-[20px] py-[12px] text-left">Provider</th>
                  <th className="px-[20px] py-[12px] text-left">Purpose</th>
                  <th className="px-[20px] py-[12px] text-left">Region</th>
                </tr>
              </thead>
              <tbody className="text-[14px] text-[#191919]">
                <SubprocessorRow
                  name="Anthropic"
                  purpose="LLM inference (Claude API). Does not train on prompts."
                  region="US"
                />
                <SubprocessorRow
                  name="Neon"
                  purpose="Managed Postgres for application data."
                  region="US"
                />
                <SubprocessorRow
                  name="Render"
                  purpose="Backend hosting + cron scheduling."
                  region="US"
                />
                <SubprocessorRow
                  name="Cloudflare"
                  purpose="Marketing site CDN + DNS."
                  region="Global edge"
                />
                <SubprocessorRow
                  name="Clerk"
                  purpose="Web authentication, MFA, session management."
                  region="US"
                />
                <SubprocessorRow
                  name="Pipedream"
                  purpose="Integration gateway (3,200+ apps via OAuth)."
                  region="US"
                />
                <SubprocessorRow
                  name="Composio"
                  purpose="Integration gateway (overlap with Pipedream, used for Metabase + a few apps)."
                  region="US"
                />
                <SubprocessorRow
                  name="Langfuse"
                  purpose="Observability of LLM calls (trace metadata, sanitized payloads)."
                  region="US"
                />
                <SubprocessorRow
                  name="Stripe"
                  purpose="Billing + payment processing (Business and below)."
                  region="US"
                  last
                />
              </tbody>
            </table>
          </div>
          <p className="font-[family-name:var(--font-inter)] text-[13px] leading-[1.55] text-[#626262]">
            Updated lists, exact API scopes per provider, and the security
            questionnaire are available on request for Business / Enterprise
            evaluations. Email us at{" "}
            <a
              href="mailto:security@misterr.ai"
              className="font-medium text-[#ff5200] underline-offset-4 hover:underline"
            >
              security@misterr.ai
            </a>
            .
          </p>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="flex w-full flex-col items-center gap-[16px] bg-[#faf5f1] px-[24px] py-[80px] text-center">
        <h2 className="max-w-[640px] font-[family-name:var(--font-lexend)] text-[28px] font-semibold tracking-[-1px] text-[#191919] sm:text-[36px]">
          ¿Algo que tu equipo de security necesita ver?
        </h2>
        <p className="max-w-[520px] font-[family-name:var(--font-inter)] text-[16px] text-[#626262]">
          Mandanos tu cuestionario o agendá una llamada. Respondemos rápido y
          sin marketing.
        </p>
        <div className="mt-[8px] flex flex-wrap items-center justify-center gap-[10px]">
          <a
            href={BOOK_SALES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-[12px] bg-[#ff5200] px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-white shadow-[0px_4px_0px_0px_#cc4a00] transition active:translate-y-[2px] hover:bg-[#ff6a23]"
          >
            Agendar llamada
          </a>
          <a
            href="mailto:security@misterr.ai"
            className="rounded-[12px] border border-[#191919] bg-white px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-[#191919] transition hover:bg-[#191919] hover:text-white"
          >
            security@misterr.ai
          </a>
          <Link
            href="/privacy"
            className="rounded-[12px] border border-[#191919] bg-white px-[18px] py-[12px] font-[family-name:var(--font-inter)] text-[15px] font-semibold text-[#191919] transition hover:bg-[#191919] hover:text-white"
          >
            Privacy policy
          </Link>
        </div>
      </section>

      <Footer />
    </main>
  );
}


function ControlsSection({
  eyebrow,
  title,
  description,
  controls,
  tint,
}: {
  eyebrow: string;
  title: string;
  description: string;
  controls: Control[];
  tint?: string;
}) {
  return (
    <section
      className={`flex w-full flex-col items-center ${tint ?? "bg-white"} px-[24px] py-[60px]`}
    >
      <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-[28px]">
        <div className="flex flex-col gap-[8px]">
          <p className="font-[family-name:var(--font-inter)] text-[12px] font-semibold uppercase tracking-[2px] text-[#4a4a4a]">
            {eyebrow}
          </p>
          <h2 className="font-[family-name:var(--font-lexend)] text-[28px] font-semibold tracking-[-1px] text-[#191919] sm:text-[36px]">
            {title}
          </h2>
          <p className="max-w-[680px] font-[family-name:var(--font-inter)] text-[16px] leading-[1.5] text-[#4a4a4a]">
            {description}
          </p>
        </div>
        <div className="grid gap-[16px] sm:grid-cols-2">
          {controls.map((c) => (
            <div
              key={c.title}
              className="flex flex-col gap-[8px] rounded-[16px] border border-[#191919] bg-white p-[24px] shadow-[0px_4px_0px_0px_#626262]"
            >
              <h3 className="font-[family-name:var(--font-lexend)] text-[18px] font-semibold tracking-[-0.5px] text-[#191919]">
                {c.title}
              </h3>
              <p className="font-[family-name:var(--font-inter)] text-[14px] leading-[1.5] text-[#4a4a4a]">
                {c.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}


function SubprocessorRow({
  name,
  purpose,
  region,
  last,
}: {
  name: string;
  purpose: string;
  region: string;
  last?: boolean;
}) {
  return (
    <tr className={last ? "" : "border-b border-[#e1dfde]"}>
      <td className="px-[20px] py-[12px] font-semibold">{name}</td>
      <td className="px-[20px] py-[12px] text-[#4a4a4a]">{purpose}</td>
      <td className="px-[20px] py-[12px] text-[#4a4a4a]">{region}</td>
    </tr>
  );
}
