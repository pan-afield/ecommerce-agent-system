import eslint from "@eslint/js";
import eslintConfigPrettier from "eslint-config-prettier";
import globals from "globals";
import tseslint from "typescript-eslint";

export const baseConfig = [
  {
    ignores: ["**/.next/**", "**/.turbo/**", "**/dist/**", "**/node_modules/**"],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  eslintConfigPrettier,
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": [
        "error",
        { fixStyle: "inline-type-imports" },
      ],
    },
  },
];

export default baseConfig;
