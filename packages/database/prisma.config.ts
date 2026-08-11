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
  schema: "prisma/schema.prisma",
  migrations: {
    path: "prisma/migrations",
    seed: "node prisma/seed.mjs",
  },
  datasource: {
    url: process.env.DATABASE_URL,
  },
});
