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

// Google Calendar Appointment Scheduling page for sales chats. Every
// "Talk to sales" / "Hablar con sales" CTA across the marketing site
// links here (opened in a new tab via target="_blank"). Replaces the
// previous `mailto:sales@misterr.ai` flow.
//
// Override via NEXT_PUBLIC_BOOK_SALES_URL if/when we move off Google
// Calendar (e.g. to Cal.com or Calendly). The Google URL doubles as
// a popup-style booking widget when clicked, so we don't need to
// embed Google's script tag globally -- the URL alone gives the same
// scheduling UX in a new tab.
export const BOOK_SALES_URL =
  process.env.NEXT_PUBLIC_BOOK_SALES_URL ??
  "https://calendar.google.com/calendar/appointments/schedules/AcZssZ3YyieoxXJ31fHz8KGKaCY46XrQFcs9xtbNRuVqiy3hIRuHOTcN8m7ERXgIWmNS2N6EVyYsaZ0N?gv=true";
