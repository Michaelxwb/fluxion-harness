import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "scripts/**/*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: {
        CanvasRenderingContext2D: "readonly",
        HTMLCanvasElement: "readonly",
        document: "readonly",
        window: "readonly"
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
        sourceType: "module"
      }
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "no-undef": "off"
    }
  }
];
