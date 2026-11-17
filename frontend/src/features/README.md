# Features

Product code is grouped by **feature**, one folder per area of the product
(EPIC-01 §7):

```
features/
  campaigns/      list, new campaign, workspace tabs, hooks
  applications/   candidates table, filters, review panel, bulk actions
  uploads/        dropzone, processing list, useCampaignEvents
  people/         person profile, talent list
  home/
```

Folders are created by the tickets that need them (E1-03 onwards).

## Rules

1. **Routes compose features.** Files in `src/app` stay thin: read params,
   pick a feature component, render it.
2. **Features never import each other.** Code in `src/features/<a>/**` may not
   import from `src/features/<b>/**`. If two features need the same thing, it
   is shared code: move it to
   - `src/components/` for UI (`ui/` holds the shadcn primitives,
     `app-shell/` the sidebar, top bar and ⌘K palette), or
   - `src/lib/` for non-UI code (API client, query keys, utilities).
3. **No `axios` or `react-toastify`** in features. Use the generated API client
   in `src/lib/api` (E1-03) and `toast` from `sonner`.

Inside a feature, import freely, relatively or via `@/features/<same>/…`.

## Enforcement

`eslint.config.mjs` generates a `no-restricted-imports` block for every folder
in `src/features`, so a new feature is covered automatically. `npm run lint`
fails on a violation, for example:

```
src/features/campaigns/list.tsx
  1:1  error  '@/features/people/hooks' import is restricted from being used by a pattern.
       src/features/campaigns may not import another feature. Move shared code to
       src/components or src/lib (see src/features/README.md)  no-restricted-imports
```

This is the frontend counterpart of the backend's import-linter contracts.
