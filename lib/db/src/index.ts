import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import * as schema from "./schema";

const { Pool } = pg;

if (!process.env.NEON_DATABASE_URL) {
  throw new Error(
    "NEON_DATABASE_URL must be set. Did you forget to provision a database?",
  );
}

export const pool = new Pool({ connectionString: process.env.NEON_DATABASE_URL });
export const db = drizzle(pool, { schema });

export * from "./schema";
