import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy — Misterr",
  description: "Privacy Policy describing how Misterr handles your data.",
};

const LAST_UPDATED = "May 30, 2026";

export default function PrivacyPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col px-6 py-16">
      <nav className="mb-10 text-sm">
        <Link
          href="/"
          className="text-neutral-600 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        >
          ← Back to home
        </Link>
      </nav>

      <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
        Privacy Policy
      </h1>
      <p className="mt-2 text-sm text-neutral-500 dark:text-neutral-400">
        Last updated: {LAST_UPDATED}
      </p>

      <div className="prose prose-neutral mt-10 max-w-none dark:prose-invert">
        <p>
          This Privacy Policy describes how Misterr (&ldquo;we&rdquo;,
          &ldquo;us&rdquo;, or &ldquo;our&rdquo;) collects, uses, and shares
          information when you use our website, applications, and services
          (the &ldquo;Service&rdquo;).
        </p>

        <h2 className="mt-8 text-2xl font-semibold">
          1. Information We Collect
        </h2>
        <p>We collect the following categories of information:</p>
        <ul className="list-disc pl-6">
          <li>
            <strong>Account information:</strong> name, email address, and
            authentication identifiers you provide when creating an account or
            connecting Slack.
          </li>
          <li>
            <strong>Workspace and conversation data:</strong> messages,
            channels, files, and metadata that you authorize Misterr to access
            in order to provide the Service.
          </li>
          <li>
            <strong>Usage data:</strong> log data, device information, IP
            address, and interactions with the Service.
          </li>
          <li>
            <strong>Cookies and similar technologies:</strong> used for
            authentication, preferences, and analytics.
          </li>
        </ul>

        <h2 className="mt-8 text-2xl font-semibold">
          2. How We Use Information
        </h2>
        <p>We use the information we collect to:</p>
        <ul className="list-disc pl-6">
          <li>Provide, maintain, and improve the Service.</li>
          <li>Authenticate users and prevent abuse.</li>
          <li>Communicate with you about updates and support.</li>
          <li>Comply with legal obligations and enforce our Terms.</li>
        </ul>

        <h2 className="mt-8 text-2xl font-semibold">
          3. Sharing of Information
        </h2>
        <p>
          We do not sell your personal information. We may share information
          with:
        </p>
        <ul className="list-disc pl-6">
          <li>
            <strong>Service providers</strong> that help us operate the Service
            (e.g., hosting, authentication, analytics), under contractual
            obligations consistent with this Policy.
          </li>
          <li>
            <strong>Legal and safety</strong> recipients when required by law
            or to protect the rights, property, or safety of Misterr, our
            users, or others.
          </li>
          <li>
            <strong>Business transfers</strong> in connection with a merger,
            acquisition, or asset sale, subject to standard confidentiality
            commitments.
          </li>
        </ul>

        <h2 className="mt-8 text-2xl font-semibold">
          4. AI Processing
        </h2>
        <p>
          Misterr uses large language models to process your workspace content
          and generate responses. Where third-party model providers are
          involved, we configure their services to disable training on your
          content where supported.
        </p>

        <h2 className="mt-8 text-2xl font-semibold">5. Data Retention</h2>
        <p>
          We retain personal information for as long as needed to provide the
          Service, comply with our legal obligations, resolve disputes, and
          enforce our agreements. You may request deletion of your account at
          any time.
        </p>

        <h2 className="mt-8 text-2xl font-semibold">6. Security</h2>
        <p>
          We implement technical and organizational measures designed to
          protect personal information. However, no method of transmission or
          storage is completely secure, and we cannot guarantee absolute
          security.
        </p>

        <h2 className="mt-8 text-2xl font-semibold">7. Your Rights</h2>
        <p>
          Depending on your jurisdiction, you may have the right to access,
          correct, delete, port, or restrict processing of your personal
          information, as well as the right to object to certain processing.
          To exercise these rights, contact us at the address below.
        </p>

        <h2 className="mt-8 text-2xl font-semibold">
          8. International Transfers
        </h2>
        <p>
          Your information may be processed in countries other than the one in
          which you reside. We take steps to ensure appropriate safeguards are
          in place for such transfers.
        </p>

        <h2 className="mt-8 text-2xl font-semibold">
          9. Changes to this Policy
        </h2>
        <p>
          We may update this Privacy Policy from time to time. Changes will be
          posted on this page with an updated &ldquo;Last updated&rdquo; date.
        </p>

        <h2 className="mt-8 text-2xl font-semibold">10. Contact</h2>
        <p>
          For questions about this Privacy Policy or our data practices,
          contact us at{" "}
          <a
            href="mailto:privacy@misterr.ai"
            className="underline hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            privacy@misterr.ai
          </a>
          .
        </p>
      </div>
    </main>
  );
}
