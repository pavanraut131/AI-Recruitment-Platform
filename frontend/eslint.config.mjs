import { readdirSync } from "node:fs"
import { defineConfig, globalIgnores } from "eslint/config"
import nextVitals from "eslint-config-next/core-web-vitals"
import nextTs from "eslint-config-next/typescript"

/*
 * Feature-folder boundaries (EPIC-01 §7, src/features/README.md).
 *
 * Code in src/features/<a>/** may not import src/features/<b>/** — shared
 * pieces live in src/components or src/lib. No code in src may use the
 * libraries the new frontend replaced (axios → the generated /api/v2 client,
 * react-toastify → sonner); the old UI and its v1 client went in E1-10.
 *
 * One config block per feature folder, generated from the file system, so a
 * new feature is covered without editing this file. Each block repeats the
 * banned libraries because a later block's rule options replace earlier ones.
 */
const FEATURES_DIR = new URL("./src/features/", import.meta.url)

const featureNames = (() => {
  try {
    return readdirSync(FEATURES_DIR, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
  } catch {
    return []
  }
})()

const bannedLibraries = [
  {
    name: "axios",
    message: "Use the generated API client in src/lib/api (EPIC-01 §7).",
  },
  {
    name: "react-toastify",
    message: "Use toast from 'sonner' (EPIC-01 §7).",
  },
]
const bannedLibraryPatterns = [
  {
    group: ["axios/*", "react-toastify/*"],
    message: "axios and react-toastify are replaced in the new frontend (EPIC-01 §7).",
  },
]

function featureBoundary(feature) {
  const others = featureNames.filter((name) => name !== feature)
  if (others.length === 0) return []
  const relativeUps = ["..", "../..", "../../..", "../../../.."]
  return [
    {
      group: others.flatMap((other) => [
        `@/features/${other}`,
        `@/features/${other}/**`,
        // Relative climbs out of this feature into a sibling feature.
        ...relativeUps.flatMap((up) => [`${up}/${other}`, `${up}/${other}/**`]),
      ]),
      message: `src/features/${feature} may not import another feature. Move shared code to src/components or src/lib (see src/features/README.md).`,
    },
  ]
}

const featureRules = [
  {
    name: "hireflow/banned-libraries",
    files: ["src/**/*.{ts,tsx,js,jsx,mjs}"],
    rules: {
      "no-restricted-imports": [
        "error",
        { paths: bannedLibraries, patterns: bannedLibraryPatterns },
      ],
    },
  },
  ...featureNames.map((feature) => ({
    name: `hireflow/feature-boundary/${feature}`,
    files: [`src/features/${feature}/**/*.{ts,tsx,js,jsx,mjs}`],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: bannedLibraries,
          patterns: [...bannedLibraryPatterns, ...featureBoundary(feature)],
        },
      ],
    },
  })),
]

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  ...featureRules,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Playwright's output (the HTML report bundles its own JS).
    "e2e-results/**",
  ]),
])

export default eslintConfig
