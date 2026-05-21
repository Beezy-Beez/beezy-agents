import { defineConfig } from "drizzle-kit";
import path from "path";

if (!process.env.NEON_DATABASE_URL) {
  throw new Error("NEON_DATABASE_URL, ensure the database is provisioned");
}

export default defineConfig({
  schema: path.join(__dirname, "./src/schema/index.ts"),
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.NEON_DATABASE_URL,
  },
});
