"use client";

// Unified modal for "View" (read-only) and "Upload new" flows.
// Mode is implicit: when a `skillName` prop is given, we fetch the detail
// and render read-only. Otherwise we render the upload form.

import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Upload, FileText, Lock } from "lucide-react";

import {
  parseSkillMarkdown,
  skillsApi,
  type SkillCreateInput,
  type SkillDetail,
  type SkillListResponse,
} from "@/lib/api/skills";

type Props =
  | { mode: "view"; skillName: string; onClose: () => void }
  | { mode: "upload"; onClose: () => void };

const SKILLS_LIST_KEY = ["skills", "all"] as const;

export function SkillModal(props: Props) {
  if (props.mode === "view") {
    return <ViewModal skillName={props.skillName} onClose={props.onClose} />;
  }
  return <UploadModal onClose={props.onClose} />;
}

// --------------------------------------------------------------------------- //
// View (read-only)
// --------------------------------------------------------------------------- //

function ViewModal({
  skillName,
  onClose,
}: {
  skillName: string;
  onClose: () => void;
}) {
  const { getToken } = useAuth();

  const detailQuery = useQuery({
    queryKey: ["skills", "detail", skillName],
    queryFn: async (): Promise<SkillDetail> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return skillsApi.detail(skillName, token);
    },
  });

  return (
    <ModalShell
      title={skillName}
      subtitle={detailQuery.data?.description}
      onClose={onClose}
    >
      {detailQuery.isLoading ? (
        <div className="flex h-40 items-center justify-center text-sm text-neutral-500">
          Loading…
        </div>
      ) : detailQuery.isError ? (
        <ErrorBlock
          message={(detailQuery.error as Error)?.message ?? "Unknown error"}
        />
      ) : detailQuery.data ? (
        <ViewBody detail={detailQuery.data} />
      ) : null}
    </ModalShell>
  );
}

function ViewBody({ detail }: { detail: SkillDetail }) {
  return (
    <div className="flex flex-col gap-4">
      <MetadataGrid detail={detail} />
      <div>
        <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-neutral-500">
          .md content
        </div>
        <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap rounded-md border border-[var(--color-border)] bg-[var(--color-surface-fog)] p-3 font-mono text-[12px] leading-relaxed text-[var(--color-ink-deep)]">
          {detail.body || "(empty)"}
        </pre>
      </div>
    </div>
  );
}

function MetadataGrid({ detail }: { detail: SkillDetail }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs text-neutral-600 sm:grid-cols-3">
      <MetaField label="Scope">
        <span className="inline-flex items-center gap-1">
          {detail.scope === "personal" ? (
            <>
              <Lock className="size-3" strokeWidth={2} />
              Personal
            </>
          ) : (
            "Workspace"
          )}
        </span>
      </MetaField>
      <MetaField label="Activation">
        {detail.effective_activation === "always_active"
          ? "Always on"
          : "On demand"}
      </MetaField>
      <MetaField label="Status">
        {detail.is_installed ? "Installed" : "Not installed"}
      </MetaField>
      <MetaField label="Version">v{detail.version}</MetaField>
      <MetaField label="Size">
        {Math.max(1, Math.round(detail.size_bytes / 1024))} KB
      </MetaField>
      <MetaField label="Source">{detail.source}</MetaField>
    </dl>
  );
}

function MetaField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </dt>
      <dd className="text-[12px] text-neutral-800">{children}</dd>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Upload
// --------------------------------------------------------------------------- //

function UploadModal({ onClose }: { onClose: () => void }) {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [activation, setActivation] = useState<"always_active" | "on_demand">(
    "on_demand",
  );
  // Default to personal per the prior product decision: privacy-first.
  const [scope, setScope] = useState<"workspace" | "personal">("personal");
  const [body, setBody] = useState("");
  const [linksRaw, setLinksRaw] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function handleFileText(text: string, originName?: string) {
    setError(null);
    setFileName(originName ?? null);
    const { frontmatter, body: parsedBody } = parseSkillMarkdown(text);
    if (frontmatter.name && !name) setName(frontmatter.name);
    if (frontmatter.description && !description)
      setDescription(frontmatter.description);
    if (frontmatter.activation) setActivation(frontmatter.activation);
    if (frontmatter.scope) setScope(frontmatter.scope);
    if (frontmatter.links?.length)
      setLinksRaw(frontmatter.links.join(", "));
    setBody(parsedBody.trim());
    // If the filename gives us a name hint and frontmatter didn't, use it.
    if (!frontmatter.name && originName && !name) {
      const guess = originName.replace(/\.md$/i, "").toLowerCase().replace(/[^a-z0-9-]+/g, "-");
      setName(guess.replace(/^-+|-+$/g, ""));
    }
  }

  async function readFile(file: File) {
    const text = await file.text();
    handleFileText(text, file.name);
  }

  const createMut = useMutation({
    mutationFn: async (input: SkillCreateInput): Promise<SkillDetail> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return skillsApi.create(input, token);
    },
    onSuccess: () => {
      // Invalidate so the list refreshes with the new row.
      queryClient.invalidateQueries({ queryKey: SKILLS_LIST_KEY });
      onClose();
    },
    onError: (err: Error & { status?: number }) => {
      setError(err.message || "Unknown error");
    },
  });

  function submit() {
    setError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Name is required.");
      return;
    }
    if (!description.trim()) {
      setError("Description is required.");
      return;
    }
    if (!body.trim()) {
      setError("Content (.md) can't be empty.");
      return;
    }
    const links = linksRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    createMut.mutate({
      name: trimmedName,
      description: description.trim(),
      activation_default: activation,
      scope,
      body,
      links,
    });
  }

  return (
    <ModalShell
      title="Upload skill"
      subtitle="Drag in a .md or fill the fields manually."
      onClose={onClose}
    >
      <div className="flex flex-col gap-4">
        <DropZone
          onFile={readFile}
          fileName={fileName}
          fileInputRef={fileInputRef}
        />

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <TextField
            label="Name (kebab-case slug)"
            value={name}
            onChange={setName}
            placeholder="my-skill"
            mono
          />
          <TextField
            label="Description (1 line)"
            value={description}
            onChange={setDescription}
            placeholder="What this skill does in one sentence"
          />
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <RadioGroup
            label="Activation"
            value={activation}
            onChange={(v) => setActivation(v as typeof activation)}
            options={[
              { value: "on_demand", label: "On demand" },
              { value: "always_active", label: "Always active" },
            ]}
          />
          <RadioGroup
            label="Scope"
            value={scope}
            onChange={(v) => setScope(v as typeof scope)}
            options={[
              { value: "personal", label: "Personal (only you)" },
              { value: "workspace", label: "Workspace (everyone)" },
            ]}
          />
        </div>

        <TextField
          label="Tags / links (comma-separated, optional)"
          value={linksRaw}
          onChange={setLinksRaw}
          placeholder="seo, analytics"
        />

        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-neutral-500">
            .md content
          </div>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={12}
            placeholder="# My skill\n\nInstructions Misterr will read..."
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface-fog)] p-3 font-mono text-[12px] leading-relaxed text-[var(--color-ink-deep)] focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
          />
        </div>

        {error ? <ErrorBlock message={error} /> : null}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-neutral-600 hover:text-[var(--color-ink-deep)]"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={createMut.isPending}
            onClick={submit}
            className="inline-flex items-center gap-1 rounded-md bg-[#FF5200] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#e54a00] disabled:opacity-60"
          >
            <Upload className="size-3.5" strokeWidth={2} />
            {createMut.isPending ? "Uploading…" : "Upload skill"}
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

function DropZone({
  onFile,
  fileName,
  fileInputRef,
}: {
  onFile: (file: File) => void;
  fileName: string | null;
  fileInputRef: React.MutableRefObject<HTMLInputElement | null>;
}) {
  const [over, setOver] = useState(false);

  function handle(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".md")) {
      alert("We only accept .md files");
      return;
    }
    onFile(file);
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={handle}
      onClick={() => fileInputRef.current?.click()}
      className={`flex cursor-pointer items-center justify-center gap-2 rounded-md border-2 border-dashed py-6 text-sm transition-colors ${
        over
          ? "border-[#FF5200] bg-[#FF5200]/5"
          : "border-[var(--color-border)] bg-white text-neutral-500 hover:border-[#FF5200] hover:text-[var(--color-ink-deep)]"
      }`}
    >
      <FileText className="size-4" strokeWidth={1.75} />
      {fileName ? (
        <span>
          <strong className="font-medium text-[var(--color-ink-deep)]">
            {fileName}
          </strong>{" "}
          loaded · click or drop to replace
        </span>
      ) : (
        <span>Drag a .md here or click to select</span>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept=".md"
        className="hidden"
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) await onFile(f);
        }}
      />
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  mono = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm text-[var(--color-ink-deep)] focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30 ${
          mono ? "font-mono text-[12px]" : ""
        }`}
      />
    </label>
  );
}

function RadioGroup({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </span>
      <div className="flex gap-3 text-sm">
        {options.map((o) => (
          <label
            key={o.value}
            className="inline-flex cursor-pointer items-center gap-1.5"
          >
            <input
              type="radio"
              name={label}
              value={o.value}
              checked={value === o.value}
              onChange={() => onChange(o.value)}
              className="accent-[#FF5200]"
            />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Modal shell
// --------------------------------------------------------------------------- //

function ModalShell({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  // Close on Escape so the modal doesn't trap focus indefinitely.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-5 py-4">
          <div>
            <h2 className="font-mono text-base font-semibold text-[var(--color-ink-deep)]">
              {title}
            </h2>
            {subtitle ? (
              <p className="mt-0.5 text-xs text-neutral-500">{subtitle}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-neutral-500 hover:bg-[var(--color-surface-fog)] hover:text-[var(--color-ink-deep)]"
            aria-label="Close"
          >
            <X className="size-4" strokeWidth={1.75} />
          </button>
        </header>
        <div className="flex-1 overflow-auto p-5">{children}</div>
      </div>
    </div>
  );
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-3 text-[12px] text-red-700">
      {message}
    </div>
  );
}
