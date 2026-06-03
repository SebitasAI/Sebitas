// Single source of truth for the URL of the Misterr web app.
//
// The marketing site lives on the apex (`misterr.ai`); the Clerk-backed
// app lives at `app.misterr.ai`. Every CTA that sends the user into the
// product (Sign in, Sign up, Get started, Start for free, pricing
// Seleccionar buttons) should target this host.
//
// `NEXT_PUBLIC_APP_URL` lets prod / staging override the default without
// a code change. Default points at production; setting it to
// `http://localhost:3003` is the documented way to test against a local
// web app during a marketing-site preview.
export const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ?? "https://app.misterr.ai";

export const APP_SIGN_IN_URL = `${APP_URL}/sign-in`;
export const APP_SIGN_UP_URL = `${APP_URL}/sign-up`;
