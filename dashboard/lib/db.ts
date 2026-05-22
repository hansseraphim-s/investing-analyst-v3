import { neon } from "@neondatabase/serverless";

let _client: ReturnType<typeof neon> | null = null;

export function sql(strings: TemplateStringsArray, ...values: unknown[]) {
  if (!_client) {
    const dsn = process.env.NEON_DATABASE_URL;
    if (!dsn) {
      throw new Error(
        "NEON_DATABASE_URL is not set. Configure in Vercel project env vars " +
          "(or locally in dashboard/.env.local).",
      );
    }
    _client = neon(dsn);
  }
  return _client(strings, ...values);
}
