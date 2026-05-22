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
    // Vercel's Neon Postgres marketplace integration provisions DATABASE_URL.
    // Local dev / non-Vercel hosts use NEON_DATABASE_URL. Accept either.
    const dsn = process.env.DATABASE_URL ?? process.env.NEON_DATABASE_URL;
    if (!dsn) {
      throw new Error(
        "DATABASE_URL (or NEON_DATABASE_URL) is not set. Install the Neon " +
          "Postgres integration in Vercel, or set NEON_DATABASE_URL locally.",
      );
    }
    _client = neon(dsn);
  }
  return _client(strings, ...values) as unknown as Promise<T[]>;
}
