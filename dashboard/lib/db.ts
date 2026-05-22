import { neon } from "@neondatabase/serverless";

const dsn = process.env.NEON_DATABASE_URL;
if (!dsn) {
  throw new Error(
    "NEON_DATABASE_URL is not set. Copy dashboard/.env.example to .env.local and fill it in.",
  );
}

export const sql = neon(dsn);
