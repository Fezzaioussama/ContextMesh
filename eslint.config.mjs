import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores([
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/.venv-quality/**",
    "**/venv/**",
    "**/env/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.tox/**",
    "**/.nox/**",
    "**/.cache/**",
    "**/dist/**",
    "**/build/**",
    "**/coverage/**",
    "**/htmlcov/**",
    "**/.coverage_html/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.svelte-kit/**",
    "**/vendor/**",
    "**/third_party/**",
    "**/generated/**",
    "**/site-packages/**",
    "**/*.egg-info/**",
    "**/.data/**",
  ]),
  {
    files: ["**/*.{js,cjs,mjs,jsx}"],
    extends: [js.configs.recommended],
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: globals.node,
    },
  },
  {
    files: ["**/*.{ts,tsx,mts,cts}"],
    extends: [tseslint.configs.recommended],
  },
  {
    files: ["frontend/**/*.{js,cjs,mjs,jsx,ts,tsx,mts,cts}"],
    languageOptions: { globals: globals.browser },
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: ["**/backend/**", "@backend/**", "context_mesh/**", "app/**"],
          paths: ["context_mesh", "app"],
        },
      ],
    },
  },
  {
    files: ["**/*.{js,cjs,mjs,jsx,ts,tsx,mts,cts}"],
    linterOptions: {
      noInlineConfig: true,
      reportUnusedDisableDirectives: "error",
    },
    rules: {
      complexity: ["error", { max: 4, variant: "classic" }],
      "max-lines": [
        "error",
        { max: 1000, skipBlankLines: false, skipComments: false },
      ],
    },
  },
]);
