import { neon } from "@neondatabase/serverless";

let _client: ReturnType<typeof neon> | null = null;

// Force the return type to a plain row array so `const [first] = await sql`...``
// destructuring works. Neon's tagged-template return is a union (rows | result
// object | full-query-result), and the union breaks destructuring inference
// in the call sites. Pages opt into a more specific row shape via the generic.
export function sql<T = Record<string, any>>(
  strings: TemplateStringsArray,
  ...values: unknown[]
): Promise<T[]> {
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
  return _client(strings, ...values) as unknown as Promise<T[]>;
}
