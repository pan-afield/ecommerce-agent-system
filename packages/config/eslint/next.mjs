import nextPlugin from "@next/eslint-plugin-next";

import { reactConfig } from "./react.mjs";

export const nextConfig = [
  ...reactConfig,
  {
    files: ["**/*.{js,jsx,ts,tsx}"],
    plugins: {
      "@next/next": nextPlugin,
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
    },
  },
];

export default nextConfig;
