// Frontend: gated by Clerk auth, then reads space config + snapshot reactively.
//
// Anonymous viewer -> SignIn modal. After signing in, the App calls
// `claimAccess` once -- this lets users invited by email (before they signed
// up to Clerk) claim their pending row. If the user isn't in access_list,
// Convex queries throw "forbidden" and we render an access-denied screen.

import { useEffect } from "react";
import {
  SignInButton,
  SignedIn,
  SignedOut,
  UserButton,
  useUser,
} from "@clerk/clerk-react";
import { useMutation, useQuery } from "convex/react";
import { api } from "../convex/_generated/api";

function parseSpaceId(): string | null {
  const url = new URL(window.location.href);
  const q = url.searchParams.get("space");
  if (q) return q;
  const m = url.pathname.match(/\/s\/([^/]+)/);
  return m ? m[1] : null;
}

export function App() {
  const spaceId = parseSpaceId();
  return (
    <>
      <SignedOut>
        <Centered>
          <h2>Sebitas Space</h2>
          <p>Iniciá sesión para ver este Space.</p>
          <div style={{ marginTop: 16 }}>
            <SignInButton mode="modal" />
          </div>
        </Centered>
      </SignedOut>
      <SignedIn>
        {!spaceId ? (
          <Centered>No space id in URL. Usá <code>?space=&lt;uuid&gt;</code> o <code>/s/&lt;uuid&gt;</code>.</Centered>
        ) : (
          <Authed spaceId={spaceId} />
        )}
      </SignedIn>
    </>
  );
}

function Authed({ spaceId }: { spaceId: string }) {
  // Claim a pending email->user_id row if the user was invited before they
  // signed up to Clerk. Idempotent on the server side.
  const claim = useMutation(api.spaces.claimAccess);
  const { user } = useUser();
  useEffect(() => {
    if (user) {
      claim({ space_id: spaceId }).catch(() => {/* no-op: claim is best-effort */});
    }
  }, [user, spaceId, claim]);

  return <SpaceView spaceId={spaceId} />;
}

function SpaceView({ spaceId }: { spaceId: string }) {
  // useQuery surfaces server errors as thrown values via the Convex client's
  // error boundary; here we check `undefined` (loading) and `null` (no value).
  // Forbidden / not-found are caught by ErrorBoundary -> we render a denied
  // screen. To keep the component small we let the default behaviour show the
  // error message; production would wrap with a real ErrorBoundary.
  const config = useQuery(api.spaces.getSpaceConfig, { spaceId });
  const snapshot = useQuery(api.spaces.getLatestSnapshot, { spaceId });

  if (config === undefined || snapshot === undefined) {
    return <Centered>Cargando…</Centered>;
  }
  if (config === null) {
    return <Centered>Sin acceso o el Space no existe.</Centered>;
  }

  return (
    <div style={{ padding: 32, fontFamily: "system-ui", maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 style={{ margin: 0 }}>Live Space: {config.name}</h1>
          <div style={{ color: "#888", fontSize: 13, marginTop: 4 }}>
            refresh: {config.refresh_interval}s · {snapshot?.captured_at ? new Date(snapshot.captured_at).toLocaleString() : "—"}
          </div>
        </div>
        <UserButton afterSignOutUrl="/" />
      </div>
      {snapshot?.error ? (
        <div style={{ marginTop: 24, padding: 12, background: "#fee", border: "1px solid #f99", borderRadius: 6 }}>
          <strong>Refresh error:</strong> {snapshot.error}
        </div>
      ) : null}
      <SnapshotTable snapshot={snapshot} />
    </div>
  );
}

function SnapshotTable({ snapshot }: { snapshot: any }) {
  if (!snapshot || !Array.isArray(snapshot.rows) || snapshot.rows.length === 0) {
    return <p style={{ color: "#888", marginTop: 24 }}>Sin datos todavía.</p>;
  }
  const rows: Record<string, unknown>[] = snapshot.rows;
  const columns =
    Array.isArray(snapshot.schema) && snapshot.schema.length > 0
      ? snapshot.schema.map((s: any) => s.name as string)
      : Object.keys(rows[0] ?? {});
  return (
    <div style={{ marginTop: 24, overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>{columns.map((c) => <th key={c} style={th}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 500).map((r, i) => (
            <tr key={i}>{columns.map((c) => <td key={c} style={td}>{format(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {rows.length > 500 ? (
        <p style={{ color: "#888", marginTop: 8 }}>(mostrando 500 de {rows.length} filas)</p>
      ) : null}
    </div>
  );
}

function format(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ padding: 64, fontFamily: "system-ui", textAlign: "center", color: "#444" }}>
      {children}
    </div>
  );
}

const th: React.CSSProperties = {
  textAlign: "left", padding: "8px 10px", borderBottom: "2px solid #ddd",
  background: "#fafafa", fontWeight: 600,
};
const td: React.CSSProperties = {
  padding: "6px 10px", borderBottom: "1px solid #eee",
  fontFamily: "ui-monospace, SFMono-Regular, monospace", fontSize: 13,
};
