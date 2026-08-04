import reactHooks from "eslint-plugin-react-hooks";

import { baseConfig } from "./base.mjs";

export const reactConfig = [
  ...baseConfig,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.flat.recommended.rules,
    },
  },
];

export default reactConfig;
