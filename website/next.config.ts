import type { NextConfig } from "next";

// Static export so `pnpm build` writes `out/` and Cloudflare Pages can serve
// the files directly with no Node runtime. Anything that needs a runtime
// (server components with cookies/headers, Image with default loader,
// middleware) is OFF-LIMITS in this app; reach for `web/` instead.
const nextConfig: NextConfig = {
  output: "export",
  // `next/image` would otherwise try to use a runtime image optimiser; with
  // static export there's no server, so we serve images as-is.
  images: { unoptimized: true },
  // Trailing slashes match Cloudflare Pages' default routing better and avoid
  // double-redirects on subpaths.
  trailingSlash: true,
};

export default nextConfig;
