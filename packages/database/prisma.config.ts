import { resolve } from "node:path";

import { config } from "dotenv";
import { defineConfig } from "prisma/config";

const packageDirectory = import.meta.dirname;

config({
  path: resolve(packageDirectory, "../../.env"),
  quiet: true,
});
config({
  path: resolve(packageDirectory, ".env"),
  override: true,
  quiet: true,
});

export default defineConfig({
  experimental: {
    externalTables: true,
  },
  schema: "prisma/schema.prisma",
  migrations: {
    path: "prisma/migrations",
    seed: "node prisma/seed.mjs",
  },
  tables: {
    external: [
      "public.checkpoint_blobs",
      "public.checkpoint_migrations",
      "public.checkpoint_writes",
      "public.checkpoints",
    ],
  },
  datasource: {
    url: process.env.DATABASE_URL,
  },
});
