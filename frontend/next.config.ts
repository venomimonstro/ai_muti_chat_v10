import type { NextConfig } from "next";

function apiOrigin(): string {
  try {
    const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
    return new URL(base).origin;
  } catch {
    return "http://localhost:8000";
  }
}

const origin = apiOrigin();
const securityHeaders = [
  {key: "X-Content-Type-Options", value: "nosniff"},
  {key: "X-Frame-Options", value: "DENY"},
  {key: "Referrer-Policy", value: "strict-origin-when-cross-origin"},
  {key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()"},
  {key: "Cross-Origin-Opener-Policy", value: "same-origin"},
  {
    key: "Content-Security-Policy",
    value: `default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; form-action 'self'; img-src 'self' data: blob: ${origin}; connect-src 'self' ${origin}; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'`,
  },
];
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{source: "/(.*)", headers: securityHeaders}];
  },
};
export default nextConfig;
