import { defineConfig } from "vitest/config"

/*
 * Unit and component tests (EPIC-01 §9): Vitest + Testing Library in jsdom.
 *
 * - The `@/` alias comes from tsconfig.json through Vite's native
 *   `resolve.tsconfigPaths` (Vite 8 replaces the `vite-tsconfig-paths` plugin
 *   that the Next.js Vitest guide still mentions).
 * - JSX is compiled by Vite's built-in transform (automatic runtime, from
 *   tsconfig's `"jsx": "react-jsx"`). `@vitejs/plugin-react` would only add
 *   Fast Refresh, which tests do not need (and its optional Babel peers
 *   conflict with shadcn's Babel 7 in npm's resolver).
 */
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
    restoreMocks: true,
    unstubGlobals: true,
  },
})
