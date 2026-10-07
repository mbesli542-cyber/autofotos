import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Vehicle photos are private blob/object URLs or short-lived signed
      // Supabase URLs. next/image optimisation does not apply to them, and
      // we must never re-encode originals, so plain <img> is intentional.
      "@next/next/no-img-element": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Static service worker (plain browser JS, not part of the app bundle).
    "public/sw.js",
    // Python image processor (its virtualenv may contain vendored JS).
    "processor/**",
  ]),
]);

export default eslintConfig;
