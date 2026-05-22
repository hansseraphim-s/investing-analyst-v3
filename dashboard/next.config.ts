import type { NextConfig } from "next";

// Next.js 16 moved typedRoutes out of `experimental`. Also `middleware.ts`
// is renamed to `proxy.ts` in 16 but kept backward-compatible. We keep
// our middleware.ts (Clerk wants that filename) and accept the deprecation
// warning rather than rename for a one-version cycle.
const config: NextConfig = {
  typedRoutes: true,
};

export default config;
