declare global {
  interface Window {
    __ENV__?: { VITE_API_URL?: string };
  }
}

/** Origin of the platform API. Runtime value injected by the host page; empty in local dev (same-origin). */
export const API_ORIGIN = (
  (typeof window !== 'undefined' ? window.__ENV__?.VITE_API_URL : undefined) ??
  (import.meta as any).env?.VITE_API_URL ??
  ''
).replace(/\/$/, '');

/** WebSocket origin matching API_ORIGIN (http->ws, https->wss); falls back to the page origin when API_ORIGIN is empty. */
export function wsOrigin(): string {
  const base = API_ORIGIN || (typeof window !== 'undefined' ? window.location.origin : '');
  return base.replace(/^http/, 'ws');
}
