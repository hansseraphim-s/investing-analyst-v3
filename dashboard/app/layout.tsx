import { ClerkProvider, UserButton } from "@clerk/nextjs";
import Link from "next/link";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "investing-analyst-v3",
  description: "Risk-disciplined trading agent dashboard",
};

const navItems = [
  { href: "/live", label: "Live" },
  { href: "/strategies", label: "Strategies" },
  { href: "/risk", label: "Risk" },
  { href: "/journal", label: "Journal" },
  { href: "/backtest", label: "Backtest" },
  { href: "/health", label: "Health" },
] as const;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const mode = process.env.NEXT_PUBLIC_TRADING_MODE ?? "PAPER";
  const modeColor =
    mode === "LIVE"
      ? "bg-red-500/10 text-red-300 border-red-500/20"
      : "bg-amber-500/10 text-amber-300 border-amber-500/20";
  const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

  const shell = (
    <html lang="en">
      <body className="bg-neutral-950 text-neutral-100 antialiased min-h-screen font-sans">
        <header className="border-b border-neutral-800 sticky top-0 bg-neutral-950/80 backdrop-blur z-10">
          <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <Link href="/live" className="font-mono text-sm font-semibold">
                investing-analyst-v3
              </Link>
              <nav className="flex gap-4 text-sm">
                {navItems.map(({ href, label }) => (
                  <Link
                    key={href}
                    href={href}
                    className="text-neutral-400 hover:text-neutral-100 transition-colors"
                  >
                    {label}
                  </Link>
                ))}
              </nav>
            </div>
            <div className="flex items-center gap-3">
              <span
                className={`text-xs font-mono px-2 py-1 rounded border ${modeColor}`}
                title={mode === "LIVE" ? "REAL MONEY MODE" : "Paper trading"}
              >
                {mode}
              </span>
              {clerkKey ? (
                <UserButton afterSignOutUrl="/sign-in" />
              ) : (
                <span className="text-xs text-neutral-500 font-mono">
                  auth: configure NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
                </span>
              )}
            </div>
          </div>
        </header>
        <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
        <footer className="text-xs text-neutral-500 text-center py-4 border-t border-neutral-800 mt-12">
          Not financial advice. Backtest metrics describe the past, not the future.
        </footer>
      </body>
    </html>
  );

  // ClerkProvider throws if publishableKey is missing — even at prerender
  // time (Next.js prerenders /_not-found etc. with the root layout). Only
  // wrap when the key is actually present. With the key set in Vercel
  // env vars, runtime requests get authenticated normally; without it
  // (build-time, local dev without .env.local), the page renders but
  // auth is no-op until configured.
  if (!clerkKey) {
    return shell;
  }
  return <ClerkProvider publishableKey={clerkKey}>{shell}</ClerkProvider>;
}
