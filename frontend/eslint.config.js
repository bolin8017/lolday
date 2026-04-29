import js from "@eslint/js";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsparser from "@typescript-eslint/parser";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import prettierConfig from "eslint-config-prettier/flat";

export default [
  { ignores: ["dist", "node_modules", "src/api/schema.gen.ts"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsparser,
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "@typescript-eslint": tseslint,
      react,
      "react-hooks": reactHooks,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react/react-in-jsx-scope": "off",
    },
    settings: { react: { version: "detect" } },
  },
  {
    files: ["**/*.{js,cjs,mjs}"],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    // Test files: allow _-prefixed unused variables (Playwright fixture convention)
    // and React global (JSX runtime — no explicit import needed).
    // Patterns cover both "pnpm exec eslint ." from frontend/ and
    // the pre-commit invocation which passes absolute paths from repo root.
    files: ["tests/**/*.{ts,tsx}", "**/tests/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { React: "readonly" },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // playwright.config.d.ts is auto-generated; suppress empty-object-type there.
    files: ["playwright.config.d.ts"],
    rules: {
      "@typescript-eslint/no-empty-object-type": "off",
    },
  },
  prettierConfig,
];
