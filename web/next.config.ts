import type { NextConfig } from "next";

// SSR app deployed to Render. Clerk middleware needs the Node runtime, so
// NO `output: 'export'` here. Everything else stays at Next defaults; we
// only opt out of legacy `images.unoptimized` once a real image pipeline
// (Render-hosted, or external) is decided.
const nextConfig: NextConfig = {};

export default nextConfig;
